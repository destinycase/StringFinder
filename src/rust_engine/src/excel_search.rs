use crate::types::SearchMatch;
use calamine::{Data, Reader, Xls, Xlsb, Xlsx};
use std::path::Path;
use unicode_normalization::UnicodeNormalization;

const EXCEL_MARKER_SHEET_ERROR_PREFIX: &str = "__SF_EXCEL_SHEET_ERR__|";
const EXCEL_MARKER_PANIC_PREFIX: &str = "__SF_EXCEL_PANIC__|";

struct ExcelSearchContext<'a> {
    pat_lower: &'a str,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    results: &'a mut Vec<SearchMatch>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

pub fn search_excel_file(
    path: &Path,
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();

    let pat_nfc: String = pattern.chars().nfc().collect();
    let pat_lower = pat_nfc.to_lowercase();

    match ext.as_str() {
        "xlsx" | "xlsm" => {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let mut matches = Vec::new();
                let mut ctx = ExcelSearchContext {
                    pat_lower: &pat_lower,
                    ac,
                    is_exact,
                    results: &mut matches,
                    stop_flag: stop_flag.clone(),
                };
                if let Ok(mut wb) = calamine::open_workbook::<Xlsx<_>, _>(path) {
                    for sheet_name in wb.sheet_names().to_vec() {
                        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                            break;
                        }
                        match wb.worksheet_range(&sheet_name) {
                            Ok(range) => {
                                let (offset_row, offset_col) = range.start().unwrap_or((0, 0));
                                for (row_idx, row) in range.rows().enumerate() {
                                    if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                                        break;
                                    }
                                    for (col_idx, cell) in row.iter().enumerate() {
                                        process_cell(
                                            &sheet_name,
                                            offset_row as usize + row_idx,
                                            offset_col as usize + col_idx,
                                            cell,
                                            &mut ctx,
                                        );
                                    }
                                }
                            }
                            Err(e) => {
                                ctx.results.push(SearchMatch::new(
                                    0,
                                    format!("{}{}|{}", EXCEL_MARKER_SHEET_ERROR_PREFIX, sheet_name, e),
                                    None,
                                    None,
                                ));
                            }
                        }
                    }
                }
                matches
            }));
            if let Ok(m) = result {
                results.extend(m);
            } else {
                let panic_msg = if let Err(err) = result {
                    if let Some(s) = err.downcast_ref::<&str>() {
                        s.to_string()
                    } else if let Some(s) = err.downcast_ref::<String>() {
                        s.clone()
                    } else {
                        "Unknown panic".to_string()
                    }
                } else {
                    "None".to_string()
                };
                results.push(SearchMatch::new(
                    0,
                    format!("{}xlsx|{}", EXCEL_MARKER_PANIC_PREFIX, panic_msg),
                    None,
                    None,
                ));
            }
        }
        "xlsb" => {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let mut matches = Vec::new();
                let mut ctx = ExcelSearchContext {
                    pat_lower: &pat_lower,
                    ac,
                    is_exact,
                    results: &mut matches,
                    stop_flag: stop_flag.clone(),
                };
                if let Ok(mut wb) = calamine::open_workbook::<Xlsb<_>, _>(path) {
                    for sheet_name in wb.sheet_names().to_vec() {
                        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                            break;
                        }
                        if let Ok(range) = wb.worksheet_range(&sheet_name) {
                            let (offset_row, offset_col) = range.start().unwrap_or((0, 0));
                            for (row_idx, row) in range.rows().enumerate() {
                                if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                                    break;
                                }
                                for (col_idx, cell) in row.iter().enumerate() {
                                    process_cell(
                                        &sheet_name,
                                        offset_row as usize + row_idx,
                                        offset_col as usize + col_idx,
                                        cell,
                                        &mut ctx,
                                    );
                                }
                            }
                        }
                    }
                }
                matches
            }));
            if let Ok(m) = result {
                results.extend(m);
            } else {
                let panic_msg = if let Err(err) = result {
                    if let Some(s) = err.downcast_ref::<&str>() {
                        s.to_string()
                    } else if let Some(s) = err.downcast_ref::<String>() {
                        s.clone()
                    } else {
                        "Unknown panic".to_string()
                    }
                } else {
                    "None".to_string()
                };
                results.push(SearchMatch::new(
                    0,
                    format!("{}xlsb|{}", EXCEL_MARKER_PANIC_PREFIX, panic_msg),
                    None,
                    None,
                ));
            }
        }
        "xls" => {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                let mut matches = Vec::new();
                let mut ctx = ExcelSearchContext {
                    pat_lower: &pat_lower,
                    ac,
                    is_exact,
                    results: &mut matches,
                    stop_flag: stop_flag.clone(),
                };
                if let Ok(mut wb) = calamine::open_workbook::<Xls<_>, _>(path) {
                    for sheet_name in wb.sheet_names().to_vec() {
                        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                            break;
                        }
                        if let Ok(range) = wb.worksheet_range(&sheet_name) {
                            let (offset_row, offset_col) = range.start().unwrap_or((0, 0));
                            for (row_idx, row) in range.rows().enumerate() {
                                if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                                    break;
                                }
                                for (col_idx, cell) in row.iter().enumerate() {
                                    process_cell(
                                        &sheet_name,
                                        offset_row as usize + row_idx,
                                        offset_col as usize + col_idx,
                                        cell,
                                        &mut ctx,
                                    );
                                }
                            }
                        }
                    }
                }
                matches
            }));
            if let Ok(m) = result {
                results.extend(m);
            } else {
                let panic_msg = if let Err(err) = result {
                    if let Some(s) = err.downcast_ref::<&str>() {
                        s.to_string()
                    } else if let Some(s) = err.downcast_ref::<String>() {
                        s.clone()
                    } else {
                        "Unknown panic".to_string()
                    }
                } else {
                    "None".to_string()
                };
                results.push(SearchMatch::new(
                    0,
                    format!("{}xls|{}", EXCEL_MARKER_PANIC_PREFIX, panic_msg),
                    None,
                    None,
                ));
            }
        }
        _ => {}
    }
    results
}

fn process_cell(
    sheet_name: &str,
    row_idx: usize,
    col_idx: usize,
    cell: &Data,
    ctx: &mut ExcelSearchContext<'_>,
) {
    let cell_val = match cell {
        Data::String(s) => s.to_string(),
        Data::Float(f) => {
            if (f - f.round()).abs() < 1e-10 {
                format!("{:.0}", f)
            } else {
                f.to_string()
            }
        }
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => b.to_string(),
        _ => "".to_string(),
    };
    if cell_val.is_empty() {
        return;
    }

    let is_match = if cell_val.is_ascii() {
        // ASCII는 NFC 정규화가 불필요함
        if ctx.is_exact {
            cell_val.to_lowercase() == ctx.pat_lower
        } else {
            ctx.ac.find(&cell_val).is_some()
        }
    } else {
        let cell_val_nfc: String = cell_val.chars().nfc().collect();
        if ctx.is_exact {
            cell_val_nfc.to_lowercase() == ctx.pat_lower
        } else {
            ctx.ac.find(&cell_val_nfc).is_some()
        }
    };

    if is_match {
        let mut col_letter = String::new();
        let mut temp_col = col_idx as i32;
        while temp_col >= 0 {
            col_letter.insert(0, (b'A' + (temp_col % 26) as u8) as char);
            temp_col = (temp_col / 26) - 1;
        }
        let location = format!(
            "{}\t{}{}\t{}",
            sheet_name,
            col_letter,
            row_idx + 1,
            cell_val
        );
        ctx.results.push(SearchMatch::new(row_idx + 1, location, None, None));
    }
}
