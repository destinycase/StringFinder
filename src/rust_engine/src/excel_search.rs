use crate::types::RawMatch;
use calamine::{Cell, Data, Range, Reader, Xls, Xlsb, Xlsx, XlsxError};
use std::borrow::Cow;
use std::fmt::Write;
use std::path::Path;

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
    /// 값 읽기 전용(시트 읽기 API)일 때는 비교기가 없습니다.
    matcher: Option<&'a crate::textkey::Matcher>,
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
    let mut cell_buffer = String::with_capacity(64);
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
                    if let Some(m) =
                        match_cell(cell, &sheet_name, row_idx, col_idx, ctx, &mut cell_buffer)
                    {
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

/// 공개 검색 함수
pub fn search_excel_file(
    path: &Path,
    matcher: &crate::textkey::Matcher,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
) -> Result<Vec<RawMatch>, ExcelFileError> {
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();
    let ctx = ExcelCtx {
        path,
        date_cells: std::cell::RefCell::new(std::collections::HashMap::new()),
        matcher: Some(matcher),
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

/// 셀을 매치하고 RawMatch 반환 (검색 경로용)
fn match_cell(
    cell: &Data,
    sheet_name: &str,
    row_idx: usize,
    col_idx: usize,
    ctx: &ExcelCtx<'_>,
    cell_buffer: &mut String,
) -> Option<RawMatch> {
    let val = search_cell_text(cell, sheet_name, row_idx, col_idx, ctx, cell_buffer)?;
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

/// Borrow string cells for comparison; only stored results need owned text.
fn search_cell_text<'a>(
    cell: &'a Data,
    sheet: &str,
    row: usize,
    col: usize,
    ctx: &ExcelCtx<'_>,
    buffer: &'a mut String,
) -> Option<Cow<'a, str>> {
    match cell {
        Data::String(value) if value.is_empty() => None,
        Data::String(value) => Some(Cow::Borrowed(value)),
        Data::Int(value) => {
            buffer.clear();
            write!(buffer, "{value}").expect("writing to String cannot fail");
            Some(Cow::Borrowed(buffer))
        }
        Data::Bool(value) => Some(Cow::Borrowed(if *value { "true" } else { "false" })),
        Data::Float(value) if value.fract() == 0.0 => {
            buffer.clear();
            write!(buffer, "{value:.0}").expect("writing to String cannot fail");
            Some(Cow::Borrowed(buffer))
        }
        Data::Float(value) => {
            buffer.clear();
            write!(buffer, "{value:?}").expect("writing to String cannot fail");
            if let Some(index) = buffer.find('e') {
                let exponent: i32 = buffer[index + 1..]
                    .parse()
                    .expect("float exponent is numeric");
                buffer.truncate(index + 1);
                write!(buffer, "{exponent:+03}").expect("writing to String cannot fail");
            }
            Some(Cow::Borrowed(buffer))
        }
        Data::DateTime(value) if !(0.0..1.0).contains(&value.as_f64()) => {
            buffer.clear();
            write_excel_datetime(buffer, value).expect("writing to String cannot fail");
            Some(Cow::Borrowed(buffer))
        }
        _ => cell_text(cell, sheet, row, col, ctx).map(Cow::Owned),
    }
}

/// Convert cell values while preserving date-style handling.
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
    // 모든 형식이 같은 비교 기준(textkey)을 씁니다.
    let Some(matcher) = ctx.matcher else {
        return false;
    };
    if ctx.is_exact {
        matcher.equals_trimmed(val)
    } else {
        matcher.contains(val)
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
    let mut result = String::with_capacity(64);
    write_excel_datetime(&mut result, value).expect("writing to String cannot fail");
    result
}

fn write_excel_datetime(buffer: &mut String, value: &calamine::ExcelDateTime) -> std::fmt::Result {
    fn suffix(buffer: &mut String, millis: u16) -> std::fmt::Result {
        if millis != 0 {
            write!(buffer, ".{:06}", u32::from(millis) * 1000)?;
        }
        Ok(())
    }
    if value.is_duration() {
        let millis = (value.as_f64() * 86_400_000.0).round() as i64;
        let days = millis.div_euclid(86_400_000);
        let remainder = millis.rem_euclid(86_400_000);
        if days != 0 {
            write!(
                buffer,
                "{days} day{}, ",
                if days == 1 || days == -1 { "" } else { "s" }
            )?;
        }
        write!(
            buffer,
            "{}:{:02}:{:02}",
            remainder / 3_600_000,
            remainder / 60_000 % 60,
            remainder / 1000 % 60
        )?;
        return suffix(buffer, (remainder % 1000) as u16);
    }
    let (year, month, mut day, hour, minute, second, millis) = value.to_ymd_hms_milli();
    // Python's calendar cannot represent Excel's fictitious leap day.
    if year == 1900 && month == 2 && day == 29 {
        day = 28;
    }
    if (0.0..1.0).contains(&value.as_f64()) {
        write!(buffer, "{hour:02}:{minute:02}:{second:02}")?;
        suffix(buffer, millis)
    } else if hour == 0 && minute == 0 && second == 0 && millis == 0 {
        write!(buffer, "{year:04}-{month:02}-{day:02}")
    } else {
        write!(
            buffer,
            "{year:04}-{month:02}-{day:02} {hour:02}:{minute:02}:{second:02}"
        )?;
        suffix(buffer, millis)
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
        let matcher = crate::textkey::Matcher::new(pattern).expect("valid test pattern");
        let ctx = ExcelCtx {
            path: Path::new("test.xlsx"),
            date_cells: std::cell::RefCell::new(std::collections::HashMap::new()),
            matcher: Some(&matcher),
            is_exact: exact,
            stop_flag: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
            max_per_file: 5_000,
        };
        assert_eq!(
            cell_text(&cell, "Sheet", 0, 0, &ctx).is_some_and(|v| cell_matches_val(&v, &ctx)),
            expected
        );
        let mut buffer = String::with_capacity(64);
        let text = search_cell_text(&cell, "Sheet", 0, 0, &ctx, &mut buffer);
        assert_eq!(
            text.as_deref(),
            cell_text(&cell, "Sheet", 0, 0, &ctx).as_deref()
        );
        assert_eq!(
            text.as_deref().is_some_and(|v| cell_matches_val(v, &ctx)),
            expected
        );
        if matches!(&cell, Data::String(value) if !value.is_empty()) {
            assert!(matches!(text, Some(Cow::Borrowed(_))));
        }
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

    #[test]
    fn buffered_datetime_preserves_duration_and_time_display() {
        use calamine::{ExcelDateTime, ExcelDateTimeType};
        let mut buffer = String::with_capacity(64);
        for (serial, kind, expected) in [
            (2.5, ExcelDateTimeType::TimeDelta, "2 days, 12:00:00"),
            (1.0, ExcelDateTimeType::TimeDelta, "1 day, 0:00:00"),
            (-1.0, ExcelDateTimeType::TimeDelta, "-1 day, 0:00:00"),
            (-1.5, ExcelDateTimeType::TimeDelta, "-2 days, 12:00:00"),
            (0.5, ExcelDateTimeType::DateTime, "12:00:00"),
            (0.0, ExcelDateTimeType::DateTime, "00:00:00"),
        ] {
            buffer.clear();
            write_excel_datetime(&mut buffer, &ExcelDateTime::new(serial, kind, false)).unwrap();
            assert_eq!(buffer, expected);
        }
    }

    #[test]
    fn scalar_comparison_keeps_signed_zero_and_integer_edges() {
        for (value, expected) in [
            (Data::Float(-0.0), "-0"),
            (Data::Int(i64::MIN), "-9223372036854775808"),
            (Data::Bool(false), "false"),
            (Data::Float(1e-5), "1e-05"),
        ] {
            assert_cell_match(value, expected, true, true);
        }
    }

    #[test]
    fn fractional_buffer_matches_existing_float_representation() {
        let matcher = crate::textkey::Matcher::new("needle").unwrap();
        let ctx = ExcelCtx {
            path: Path::new("test.xlsx"),
            date_cells: Default::default(),
            matcher: Some(&matcher),
            is_exact: false,
            stop_flag: Default::default(),
            max_per_file: 10_000,
        };
        let mut buffer = String::from("old value that must not leak into the next cell");
        let mut bits = 0x1234_5678_9abc_def0_u64;
        for _ in 0..4096 {
            bits ^= bits << 13;
            bits ^= bits >> 7;
            bits ^= bits << 17;
            let cell = Data::Float(f64::from_bits(bits));
            let expected = cell_to_string(&cell);
            assert_eq!(
                search_cell_text(&cell, "Sheet", 0, 0, &ctx, &mut buffer).as_deref(),
                expected.as_deref(),
                "float bits: {bits:016x}"
            );
        }
        for value in [
            0.0,
            -0.0,
            5e-324,
            -5e-324,
            1e-5,
            1e20,
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
        ] {
            let cell = Data::Float(value);
            assert_eq!(
                search_cell_text(&cell, "Sheet", 0, 0, &ctx, &mut buffer).as_deref(),
                cell_to_string(&cell).as_deref()
            );
        }
    }

    #[test]
    fn ascii_exact_path_keeps_unicode_query_folding() {
        for (value, query, expected) in [
            ("SS", "ß", true),
            ("S", "ſ", true),
            // Unicode 대소문자 접기(C+F)에서 ı(U+0131)는 i로 접히지 않습니다(터키어 T 제외).
            // 예전 대문자화 비교와 다른 의도한 차이입니다.
            ("I", "ı", false),
            ("I", "İ", false),
            ("K", "K", true),
            (" \tNeedle\r\n", "needle", true),
            ("needles", "needle", false),
            ("é", "e\u{301}", true),
        ] {
            assert_cell_match(Data::String(value.to_owned()), query, true, expected);
        }
    }
}
