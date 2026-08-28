use crate::types::RawMatch;
use calamine::{Data, Reader, Xls, Xlsb, Xlsx};
use std::path::Path;
use unicode_normalization::UnicodeNormalization;

const EXCEL_MARKER_SHEET_ERROR_PREFIX: &str = "__SF_EXCEL_SHEET_ERR__|";
const EXCEL_MARKER_PANIC_PREFIX: &str = "__SF_EXCEL_PANIC__|";

// M3: 포맷별 공통 컨텍스트
struct ExcelCtx<'a> {
    pat_upper: &'a str,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_check_cells: u64,
}

// M3: calamine 0.33의 Reader<RS> 트레이트 시그니처에 맞게 바운드를 수정합니다.
// 이미 열린 워크북 객체를 받아 포맷 무관하게 모든 시트를 검색합니다.
fn search_wb<R, WB>(wb: &mut WB, ctx: &ExcelCtx<'_>) -> Vec<RawMatch>
where
    R: std::io::Read + std::io::Seek,
    WB: Reader<R>,
{
    let mut results: Vec<RawMatch> = Vec::new();
    for sheet_name in wb.sheet_names().to_vec() {
        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let range_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            wb.worksheet_range(&sheet_name)
        }));
        match range_result {
            Ok(Ok(range)) => {
                let (offset_row, offset_col): (u32, u32) = range.start().unwrap_or((0, 0));
                'outer: for (row_idx, row) in range.rows().enumerate() {
                    if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                        break 'outer;
                    }
                    for (col_idx, cell) in row.iter().enumerate() {
                        if results.len() > ctx.max_per_file { break 'outer; } // H2: 결과 상한
                        if let Some(m) = match_cell(
                            cell,
                            &sheet_name,
                            offset_row as usize + row_idx,
                            offset_col as usize + col_idx,
                            ctx,
                        ) {
                            results.push(m);
                        }
                    }
                }
            }
            Ok(Err(e)) => {
                results.push((0, format!("{}{}|{:?}", EXCEL_MARKER_SHEET_ERROR_PREFIX, sheet_name, e), None, None));
            }
            Err(panic_err) => {
                let msg = panic_to_string(panic_err);
                results.push((0, format!("{}{}|[Library Panic] {}", EXCEL_MARKER_SHEET_ERROR_PREFIX, sheet_name, msg), None, None));
            }
        }
        if results.len() > ctx.max_per_file { break; } // H2: 시트 간에도 확인
    }
    results
}

// M3: 존재 확인 전용 통합 헬퍼
fn check_wb<R, WB>(wb: &mut WB, ctx: &ExcelCtx<'_>) -> bool
where
    R: std::io::Read + std::io::Seek,
    WB: Reader<R>,
{
    // C1: 매우 큰 파일에서 첫 매치가 극히 마지막에 있어도 무한 순회하지 않도록 상한을 둡니다.
    let mut cell_count: u64 = 0;
    
    for sheet_name in wb.sheet_names().to_vec() {
        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            return false;
        }
        if let Ok(Ok(range)) = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            wb.worksheet_range(&sheet_name)
        })) {
            for row in range.rows() {
                if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                    return false;
                }
                for cell in row.iter() {
                    cell_count += 1;
                    if cell_count > ctx.max_check_cells { return false; } // C1: 상한 초과 시 중단
                    if cell_matches(cell, ctx) { return true; }
                }
            }
        }
    }
    false
}

/// 공개 검색 함수
pub fn search_excel_file(
    path: &Path,
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_check_cells: u64,
) -> Vec<RawMatch> {
    let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
    let pat_nfc: String = pattern.chars().nfc().collect();
    let pat_upper = pat_nfc.to_lowercase().to_uppercase();
    let ctx = ExcelCtx { pat_upper: &pat_upper, ac, is_exact, stop_flag: stop_flag.clone(), max_per_file, max_check_cells };

    // 포맷별 타입이 달라 매크로로 처리: 열기+검색을 한 번에 catch_unwind로 감쌉니다.
    macro_rules! run {
        ($open:expr, $label:expr) => {{
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                match $open {
                    Ok(mut wb) => search_wb(&mut wb, &ctx),
                    Err(_)     => vec![],
                }
            }));
            match result {
                Ok(v) => v,
                Err(p) => vec![(0, format!("{}{}|{}", EXCEL_MARKER_PANIC_PREFIX, $label, panic_to_string(p)), None, None)],
            }
        }};
    }

    match ext.as_str() {
        "xlsx" | "xlsm" => run!(calamine::open_workbook::<Xlsx<_>, _>(path), &ext),
        "xlsb"          => run!(calamine::open_workbook::<Xlsb<_>, _>(path), &ext),
        "xls"           => run!(calamine::open_workbook::<Xls<_>,  _>(path), &ext),
        _               => vec![],
    }
}

/// 공개 존재 확인 함수
pub fn check_excel_file(
    path: &Path,
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_check_cells: u64,
) -> bool {
    let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
    let pat_nfc: String = pattern.chars().nfc().collect();
    let pat_upper = pat_nfc.to_lowercase().to_uppercase();
    let ctx = ExcelCtx { pat_upper: &pat_upper, ac, is_exact, stop_flag: stop_flag.clone(), max_per_file: 5000, max_check_cells };

    macro_rules! chk {
        ($open:expr) => {{
            match $open {
                Ok(mut wb) => check_wb(&mut wb, &ctx),
                Err(_)     => false,
            }
        }};
    }

    match ext.as_str() {
        "xlsx" | "xlsm" => chk!(calamine::open_workbook::<Xlsx<_>, _>(path)),
        "xlsb"          => chk!(calamine::open_workbook::<Xlsb<_>, _>(path)),
        "xls"           => chk!(calamine::open_workbook::<Xls<_>,  _>(path)),
        _               => false,
    }
}

/// 셀을 매치하고 RawMatch 반환 (검색 경로용)
fn match_cell(cell: &Data, sheet_name: &str, row_idx: usize, col_idx: usize, ctx: &ExcelCtx<'_>) -> Option<RawMatch> {
    let val = cell_to_string(cell)?;
    if !cell_matches_val(&val, ctx) { return None; }
    // 열 인덱스를 Excel 표기법(A, B, ..., Z, AA, ...)으로 변환합니다.
    let mut col_letter = String::new();
    let mut temp = col_idx as i32;
    while temp >= 0 {
        col_letter.insert(0, (b'A' + (temp % 26) as u8) as char);
        temp = temp / 26 - 1;
    }
    Some((row_idx + 1, format!("{}\t{}{}\t{}", sheet_name, col_letter, row_idx + 1, val), None, None))
}

/// 셀 매치 여부만 확인 (존재 확인 경로용)
fn cell_matches(cell: &Data, ctx: &ExcelCtx<'_>) -> bool {
    let Some(val) = cell_to_string(cell) else { return false; };
    cell_matches_val(&val, ctx)
}

/// 문자열 값의 패턴 매치 여부 확인 공통 로직
fn cell_matches_val(val: &str, ctx: &ExcelCtx<'_>) -> bool {
    if val.is_ascii() {
        if ctx.is_exact { val.trim().to_lowercase().to_uppercase() == ctx.pat_upper }
        else { ctx.ac.find(val).is_some() }
    } else {
        let nfc: String = val.chars().nfc().collect();
        if ctx.is_exact { nfc.trim().to_lowercase().to_uppercase() == ctx.pat_upper }
        else { ctx.ac.find(&nfc).is_some() }
    }
}

/// 셀 데이터를 문자열로 변환합니다.
fn cell_to_string(cell: &Data) -> Option<String> {
    let s = match cell {
        Data::String(s) => s.to_string(),
        Data::Float(f)  => if (f - f.round()).abs() < 1e-10 { format!("{:.0}", f) } else { f.to_string() },
        Data::Int(i)    => i.to_string(),
        Data::Bool(b)   => b.to_string(),
        // 날짜/기간/오류 셀도 사용자가 확인할 수 있는 값으로 변환하여 검색 대상에 포함합니다.
        Data::DateTime(value) => value.to_string(),
        Data::DateTimeIso(value) | Data::DurationIso(value) => value.to_string(),
        Data::Error(value) => format!("{:?}", value),
        _               => return None,
    };
    if s.is_empty() { None } else { Some(s) }
}

/// panic 값을 메시지 문자열로 변환합니다.
fn panic_to_string(p: Box<dyn std::any::Any + Send>) -> String {
    if let Some(s) = p.downcast_ref::<&str>() { s.to_string() }
    else if let Some(s) = p.downcast_ref::<String>() { s.clone() }
    else { "Unknown panic".to_string() }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_date_and_duration_cells_are_searchable() {
        assert_eq!(
            cell_to_string(&Data::DateTimeIso("2026-08-28T12:30:00".to_string())),
            Some("2026-08-28T12:30:00".to_string())
        );
        assert_eq!(
            cell_to_string(&Data::DurationIso("PT1H30M".to_string())),
            Some("PT1H30M".to_string())
        );
    }
}
