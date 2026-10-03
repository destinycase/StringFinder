use crate::types::RawMatch;
use calamine::{Cell, Data, Range, Reader, Xls, Xlsb, Xlsx, XlsxError};
use std::path::Path;
use unicode_normalization::UnicodeNormalization;

const EXCEL_MARKER_SHEET_ERROR_PREFIX: &str = "__SF_EXCEL_SHEET_ERR__|";

enum SheetCells {
    Dense(Range<Data>),
    Sparse(Vec<Cell<Data>>),
}

impl SheetCells {
    fn cells(&self) -> Box<dyn Iterator<Item = (usize, usize, &Data)> + '_> {
        match self {
            Self::Dense(range) => {
                let (r, c) = range.start().unwrap_or((0, 0));
                Box::new(
                    range
                        .cells()
                        .map(move |(row, col, value)| (row + r as usize, col + c as usize, value)),
                )
            }
            Self::Sparse(cells) => Box::new(cells.iter().map(|cell| {
                let (r, c) = cell.get_position();
                (r as usize, c as usize, cell.get_value())
            })),
        }
    }
}

fn read_sparse_sheet<R: std::io::Read + std::io::Seek>(
    wb: &mut Xlsx<R>,
    name: &str,
) -> Result<Vec<Cell<Data>>, XlsxError> {
    let mut reader = match wb.worksheet_cells_reader(name) {
        Ok(reader) => reader,
        // Match worksheet_range's treatment of charts and other non-worksheets.
        Err(XlsxError::NotAWorksheet(_)) => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let mut cells = Vec::new();
    while let Some(cell) = reader.next_cell()? {
        let value = Data::from(cell.get_value().clone());
        if value != Data::Empty {
            cells.push(Cell::new(cell.get_position(), value));
        }
    }
    // Range::from_sparse orders by coordinate and the last nonempty cell wins.
    // Keep that contract even for duplicate or out-of-order XML cells.
    if !cells
        .windows(2)
        .all(|pair| pair[0].get_position() < pair[1].get_position())
    {
        cells.sort_by_key(Cell::get_position);
        cells.reverse();
        cells.dedup_by_key(|cell| cell.get_position());
        cells.reverse();
    }
    Ok(cells)
}

/// Parsed values only, never a rectangular sheet allocation. Python retains
/// ownership of precise matching/Unicode policy; this class only decodes cells.
#[pyo3::pyclass]
pub struct SparseExcelWorkbook {
    workbook: Xlsx<std::io::BufReader<std::fs::File>>,
    path: std::path::PathBuf,
}

type SparsePythonSheet = (
    Vec<(usize, usize, String)>,
    Option<(u32, u32)>,
    Option<(u32, u32)>,
);

#[pyo3::pymethods]
impl SparseExcelWorkbook {
    #[new]
    fn new(py: pyo3::Python<'_>, path: String) -> pyo3::PyResult<Self> {
        let result = py.allow_threads(|| {
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                calamine::open_workbook(&path)
            }))
        });
        let workbook = result
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(panic_to_string(e)))?
            .map_err(|e: XlsxError| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        Ok(Self {
            workbook,
            path: path.into(),
        })
    }

    #[getter]
    fn sheet_names(&self) -> Vec<String> {
        self.workbook.sheet_names()
    }

    fn read_sheet(
        &mut self,
        py: pyo3::Python<'_>,
        name: String,
    ) -> pyo3::PyResult<SparsePythonSheet> {
        let result = py.allow_threads(|| {
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let cells = read_sparse_sheet(&mut self.workbook, &name)?;
                let start = cells
                    .iter()
                    .map(Cell::get_position)
                    .reduce(|a, b| (a.0.min(b.0), a.1.min(b.1)));
                let end = cells
                    .iter()
                    .map(Cell::get_position)
                    .reduce(|a, b| (a.0.max(b.0), a.1.max(b.1)));
                let ac = aho_corasick::AhoCorasick::new([""]).expect("empty literal is valid");
                let ctx = ExcelCtx {
                    path: &self.path,
                    date_cells: Default::default(),
                    pat_upper: "",
                    ac: &ac,
                    is_exact: false,
                    stop_flag: Default::default(),
                    max_per_file: usize::MAX,
                };
                let values = cells
                    .iter()
                    .map(|cell| {
                        let (r, c) = cell.get_position();
                        // python-calamine exposes Excel error/empty cells as empty text.
                        let value = if matches!(cell.get_value(), Data::Error(_)) {
                            String::new()
                        } else {
                            cell_text(cell.get_value(), &name, r as usize, c as usize, &ctx)
                                .unwrap_or_default()
                        };
                        (r as usize, c as usize, value)
                    })
                    .collect();
                if let Some(Err(error)) = ctx.date_cells.borrow().get(&name) {
                    return Err(XlsxError::Io(std::io::Error::other(error.clone())));
                }
                Ok::<_, XlsxError>((values, start, end))
            }))
        });
        result
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(panic_to_string(e)))?
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }
}

trait SheetReader<R: std::io::Read + std::io::Seek>: Reader<R> {
    fn read_cells(&mut self, name: &str) -> Result<SheetCells, Self::Error>;
}
impl<R: std::io::Read + std::io::Seek> SheetReader<R> for Xlsx<R> {
    fn read_cells(&mut self, name: &str) -> Result<SheetCells, Self::Error> {
        read_sparse_sheet(self, name).map(SheetCells::Sparse)
    }
}
impl<R: std::io::Read + std::io::Seek> SheetReader<R> for Xls<R> {
    fn read_cells(&mut self, name: &str) -> Result<SheetCells, Self::Error> {
        self.worksheet_range(name).map(SheetCells::Dense)
    }
}
impl<R: std::io::Read + std::io::Seek> SheetReader<R> for Xlsb<R> {
    fn read_cells(&mut self, name: &str) -> Result<SheetCells, Self::Error> {
        self.worksheet_range(name).map(SheetCells::Dense)
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ExcelCheckOutcome {
    pub found: bool,
    pub sheet_error: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ExcelFileError {
    Open(String),
    Panic(String),
}

// M3: 포맷별 공통 컨텍스트
struct ExcelCtx<'a> {
    path: &'a Path,
    date_cells: std::cell::RefCell<
        std::collections::HashMap<String, Result<crate::excel_date_formats::DateCells, String>>,
    >,
    pat_upper: &'a str,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
}

// M3: calamine 0.33의 Reader<RS> 트레이트 시그니처에 맞게 바운드를 수정합니다.
// 이미 열린 워크북 객체를 받아 포맷 무관하게 모든 시트를 검색합니다.
fn search_wb<R, WB>(wb: &mut WB, ctx: &ExcelCtx<'_>) -> Vec<RawMatch>
where
    R: std::io::Read + std::io::Seek,
    WB: SheetReader<R>,
{
    let mut results: Vec<RawMatch> = Vec::new();
    let mut match_count = 0;
    for sheet_name in wb.sheet_names() {
        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let range_result =
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| wb.read_cells(&sheet_name)));
        match range_result {
            Ok(Ok(range)) => {
                'outer: for (row_idx, col_idx, cell) in range.cells() {
                    if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                        break 'outer;
                    }
                    if match_count > ctx.max_per_file {
                        break 'outer;
                    } // H2: 결과 상한
                    if let Some(m) = match_cell(cell, &sheet_name, row_idx, col_idx, ctx) {
                        results.push(m);
                        match_count += 1;
                    }
                }
            }
            Ok(Err(e)) => {
                results.push((
                    0,
                    format!("{}{}|{:?}", EXCEL_MARKER_SHEET_ERROR_PREFIX, sheet_name, e),
                    None,
                    None,
                ));
            }
            Err(panic_err) => {
                let msg = panic_to_string(panic_err);
                results.push((
                    0,
                    format!(
                        "{}{}|[Library Panic] {}",
                        EXCEL_MARKER_SHEET_ERROR_PREFIX, sheet_name, msg
                    ),
                    None,
                    None,
                ));
            }
        }
        if match_count > ctx.max_per_file {
            break;
        } // H2: 시트 간에도 확인
        if let Some(Err(error)) = ctx.date_cells.borrow().get(&sheet_name) {
            results.push((
                0,
                format!("{EXCEL_MARKER_SHEET_ERROR_PREFIX}{sheet_name}|{error}"),
                None,
                None,
            ));
        }
    }
    results
}

// M3: 존재 확인 전용 통합 헬퍼
fn check_wb<R, WB>(wb: &mut WB, ctx: &ExcelCtx<'_>) -> ExcelCheckOutcome
where
    R: std::io::Read + std::io::Seek,
    WB: SheetReader<R>,
{
    // Search all cells until the first hit or cancellation; there is no cell cap.
    let mut first_sheet_error = None;

    for sheet_name in wb.sheet_names() {
        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            return ExcelCheckOutcome::default();
        }
        if let Ok(Ok(range)) =
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| wb.read_cells(&sheet_name)))
        {
            for (row_idx, col_idx, cell) in range.cells() {
                if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                    return ExcelCheckOutcome::default();
                }
                if cell_text(cell, &sheet_name, row_idx, col_idx, ctx)
                    .is_some_and(|value| cell_matches_val(&value, ctx))
                {
                    return ExcelCheckOutcome {
                        found: true,
                        sheet_error: first_sheet_error,
                    };
                }
            }
            if let Some(Err(error)) = ctx.date_cells.borrow().get(&sheet_name) {
                first_sheet_error = Some(format!("{sheet_name}|{error}"));
            }
        } else if first_sheet_error.is_none() {
            first_sheet_error = Some(sheet_name);
        }
    }
    ExcelCheckOutcome {
        found: false,
        sheet_error: first_sheet_error,
    }
}

/// 공개 검색 함수
pub fn search_excel_file(
    path: &Path,
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
) -> Result<Vec<RawMatch>, ExcelFileError> {
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();
    let pat_nfc: String = pattern.chars().nfc().collect();
    let pat_upper = pat_nfc.to_lowercase().to_uppercase();
    let ctx = ExcelCtx {
        path,
        date_cells: std::cell::RefCell::new(std::collections::HashMap::new()),
        pat_upper: &pat_upper,
        ac,
        is_exact,
        stop_flag: stop_flag.clone(),
        max_per_file,
    };

    // 포맷별 타입이 달라 매크로로 처리: 열기+검색을 한 번에 catch_unwind로 감쌉니다.
    macro_rules! run {
        ($open:expr, $label:expr) => {{
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| match $open {
                Ok(mut wb) => Ok(search_wb(&mut wb, &ctx)),
                Err(error) => Err(ExcelFileError::Open(format!("{}|{:?}", $label, error))),
            }));
            match result {
                Ok(v) => v,
                Err(p) => Err(ExcelFileError::Panic(format!(
                    "{}|{}",
                    $label,
                    panic_to_string(p)
                ))),
            }
        }};
    }

    match ext.as_str() {
        "xlsx" | "xlsm" => run!(calamine::open_workbook::<Xlsx<_>, _>(path), &ext),
        "xlsb" => run!(calamine::open_workbook::<Xlsb<_>, _>(path), &ext),
        "xls" => run!(calamine::open_workbook::<Xls<_>, _>(path), &ext),
        _ => Err(ExcelFileError::Open(ext)),
    }
}

/// 공개 존재 확인 함수
pub fn check_excel_file(
    path: &Path,
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Result<ExcelCheckOutcome, ExcelFileError> {
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();
    let pat_nfc: String = pattern.chars().nfc().collect();
    let pat_upper = pat_nfc.to_lowercase().to_uppercase();
    let ctx = ExcelCtx {
        path,
        date_cells: std::cell::RefCell::new(std::collections::HashMap::new()),
        pat_upper: &pat_upper,
        ac,
        is_exact,
        stop_flag: stop_flag.clone(),
        max_per_file: 5000,
    };

    macro_rules! chk {
        ($open:expr, $label:expr) => {{
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| match $open {
                Ok(mut wb) => Ok(check_wb(&mut wb, &ctx)),
                Err(error) => Err(ExcelFileError::Open(format!("{}|{:?}", $label, error))),
            }));
            match result {
                Ok(outcome) => outcome,
                Err(error) => Err(ExcelFileError::Panic(format!(
                    "{}|{}",
                    $label,
                    panic_to_string(error)
                ))),
            }
        }};
    }

    match ext.as_str() {
        "xlsx" | "xlsm" => chk!(calamine::open_workbook::<Xlsx<_>, _>(path), &ext),
        "xlsb" => chk!(calamine::open_workbook::<Xlsb<_>, _>(path), &ext),
        "xls" => chk!(calamine::open_workbook::<Xls<_>, _>(path), &ext),
        _ => Err(ExcelFileError::Open(ext)),
    }
}

/// 셀을 매치하고 RawMatch 반환 (검색 경로용)
fn match_cell(
    cell: &Data,
    sheet_name: &str,
    row_idx: usize,
    col_idx: usize,
    ctx: &ExcelCtx<'_>,
) -> Option<RawMatch> {
    let val = cell_text(cell, sheet_name, row_idx, col_idx, ctx)?;
    if !cell_matches_val(&val, ctx) {
        return None;
    }
    // 열 인덱스를 Excel 표기법(A, B, ..., Z, AA, ...)으로 변환합니다.
    let mut col_letter = String::new();
    let mut temp = col_idx as i32;
    while temp >= 0 {
        col_letter.insert(0, (b'A' + (temp % 26) as u8) as char);
        temp = temp / 26 - 1;
    }
    Some((
        row_idx + 1,
        format!("{}\t{}{}\t{}", sheet_name, col_letter, row_idx + 1, val),
        None,
        None,
    ))
}

/// 셀 매치 여부만 확인 (존재 확인 경로용)
fn cell_text(
    cell: &Data,
    sheet: &str,
    row: usize,
    col: usize,
    ctx: &ExcelCtx<'_>,
) -> Option<String> {
    if let Data::DateTime(value) = cell {
        let (year, month, day, hour, minute, second, millis) = value.to_ymd_hms_milli();
        if year == 1904
            && month == 1
            && day == 1
            && (0.0..1.0).contains(&value.as_f64())
            && ctx
                .path
                .extension()
                .and_then(|e| e.to_str())
                .is_some_and(|e| e.eq_ignore_ascii_case("xlsx") || e.eq_ignore_ascii_case("xlsm"))
        {
            if let Ok(cells) = ctx
                .date_cells
                .borrow_mut()
                .entry(sheet.to_owned())
                .or_insert_with(|| crate::excel_date_formats::read_date_cells(ctx.path, sheet))
            {
                let mut letters = String::new();
                let mut index = col + 1;
                while index > 0 {
                    index -= 1;
                    letters.insert(0, (b'A' + (index % 26) as u8) as char);
                    index /= 26;
                }
                if cells.contains(&(sheet.to_owned(), format!("{letters}{}", row + 1))) {
                    if hour == 0 && minute == 0 && second == 0 && millis == 0 {
                        return Some("1904-01-01".to_owned());
                    }
                    let fraction = if millis == 0 {
                        String::new()
                    } else {
                        format!(".{:06}", millis * 1000)
                    };
                    return Some(format!(
                        "1904-01-01 {hour:02}:{minute:02}:{second:02}{fraction}"
                    ));
                }
            }
        }
    }
    cell_to_string(cell)
}

/// 문자열 값의 패턴 매치 여부 확인 공통 로직
fn cell_matches_val(val: &str, ctx: &ExcelCtx<'_>) -> bool {
    if val.is_ascii() {
        if ctx.is_exact {
            val.trim().to_lowercase().to_uppercase() == ctx.pat_upper
        } else {
            ctx.ac.find(val).is_some()
        }
    } else {
        let nfc: String = val.chars().nfc().collect();
        if ctx.is_exact {
            nfc.trim().to_lowercase().to_uppercase() == ctx.pat_upper
        } else {
            ctx.ac.find(&nfc).is_some()
        }
    }
}

/// 셀 데이터를 검색·표시용 문자열로 변환합니다.
fn cell_to_string(cell: &Data) -> Option<String> {
    let s = match cell {
        Data::String(s) => s.to_string(),
        Data::Float(f) => {
            if f.fract() == 0.0 {
                format!("{:.0}", f)
            } else {
                crate::utils::format_float(*f)
            }
        }
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => b.to_string(),
        // 날짜/기간/오류 셀도 사용자가 확인할 수 있는 값으로 변환하여 검색 대상에 포함합니다.
        Data::DateTime(value) => format_excel_datetime(value),
        Data::DateTimeIso(value) => format_iso_datetime(value),
        Data::DurationIso(value) => value.to_string(),
        Data::Error(value) => format!("{:?}", value),
        _ => return None,
    };
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

fn format_iso_datetime(value: &str) -> String {
    let Some((date, time)) = value.split_once('T') else {
        return value.to_string();
    };
    let time = if let Some((seconds, fraction)) = time.split_once('.') {
        if fraction.bytes().all(|byte| byte == b'0') {
            seconds.to_string()
        } else {
            format!("{seconds}.{fraction:0<6}")
        }
    } else {
        time.to_string()
    };
    if time == "00:00:00" || time == "00:00:00.000000" {
        date.to_string()
    } else {
        format!("{date} {time}")
    }
}

fn format_excel_datetime(value: &calamine::ExcelDateTime) -> String {
    let suffix = |millis: u16| {
        if millis == 0 {
            String::new()
        } else {
            format!(".{:06}", u32::from(millis) * 1000)
        }
    };
    if value.is_duration() {
        let millis = (value.as_f64() * 86_400_000.0).round() as i64;
        let days = millis.div_euclid(86_400_000);
        let remainder = millis.rem_euclid(86_400_000);
        let time = format!(
            "{}:{:02}:{:02}{}",
            remainder / 3_600_000,
            remainder / 60_000 % 60,
            remainder / 1000 % 60,
            suffix((remainder % 1000) as u16)
        );
        return if days == 0 {
            time
        } else {
            format!(
                "{days} day{}, {time}",
                if days == 1 || days == -1 { "" } else { "s" }
            )
        };
    }
    let (year, month, mut day, hour, minute, second, millis) = value.to_ymd_hms_milli();
    // Python's calendar cannot represent Excel's fictitious leap day.
    if year == 1900 && month == 2 && day == 29 {
        day = 28;
    }
    let time = format!("{hour:02}:{minute:02}:{second:02}{}", suffix(millis));
    if (0.0..1.0).contains(&value.as_f64()) {
        time
    } else if hour == 0 && minute == 0 && second == 0 && millis == 0 {
        format!("{year:04}-{month:02}-{day:02}")
    } else {
        format!("{year:04}-{month:02}-{day:02} {time}")
    }
}

/// panic 값을 메시지 문자열로 변환합니다.
fn panic_to_string(p: Box<dyn std::any::Any + Send>) -> String {
    if let Some(s) = p.downcast_ref::<&str>() {
        s.to_string()
    } else if let Some(s) = p.downcast_ref::<String>() {
        s.clone()
    } else {
        "Unknown panic".to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn assert_cell_match(cell: Data, pattern: &str, exact: bool, expected: bool) {
        let pat_nfc: String = pattern.chars().nfc().collect();
        let pat_upper = pat_nfc.to_lowercase().to_uppercase();
        let ac = aho_corasick::AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(aho_corasick::MatchKind::LeftmostFirst)
            .build([pat_nfc])
            .expect("valid test pattern");
        let ctx = ExcelCtx {
            path: Path::new("test.xlsx"),
            date_cells: std::cell::RefCell::new(std::collections::HashMap::new()),
            pat_upper: &pat_upper,
            ac: &ac,
            is_exact: exact,
            stop_flag: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
            max_per_file: 5_000,
        };
        assert_eq!(
            cell_text(&cell, "Sheet", 0, 0, &ctx).is_some_and(|v| cell_matches_val(&v, &ctx)),
            expected
        );
    }

    #[test]
    fn iso_date_and_duration_cells_are_searchable() {
        assert_eq!(
            cell_to_string(&Data::DateTimeIso("2026-08-28T12:30:00".to_string())),
            Some("2026-08-28 12:30:00".to_string())
        );
        assert_eq!(
            cell_to_string(&Data::DurationIso("PT1H30M".to_string())),
            Some("PT1H30M".to_string())
        );
        assert_eq!(
            format_iso_datetime("2026-08-28T12:30:00.000"),
            "2026-08-28 12:30:00"
        );
        assert_eq!(
            format_iso_datetime("2026-08-28T12:30:00.123"),
            "2026-08-28 12:30:00.123000"
        );
        assert_eq!(format_iso_datetime("2026-08-28T00:00:00.000"), "2026-08-28");
    }

    #[test]
    fn empty_string_cells_stay_excluded() {
        assert!(cell_to_string(&Data::String(String::new())).is_none());
    }

    #[test]
    fn string_path_preserves_exact_unicode_matching() {
        assert_cell_match(Data::String("STRASSE".to_string()), "straße", true, true);
        assert_cell_match(Data::String("k".to_string()), "K", true, true);
        assert_cell_match(Data::String("e\u{301}".to_string()), "é", true, true);
        assert_cell_match(
            Data::String("needle suffix".to_string()),
            "needle",
            false,
            true,
        );
        assert_cell_match(Data::String(String::new()), "needle", false, false);
    }

    #[test]
    fn owned_non_string_path_preserves_display_normalization() {
        assert_cell_match(Data::Float(10.0), "10", true, true);
        assert_cell_match(Data::Float(10.5), "10.5", true, true);
        assert_cell_match(Data::Bool(true), "true", true, true);
    }
}
