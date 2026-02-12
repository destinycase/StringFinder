use pyo3::prelude::*;
use regex::bytes::RegexBuilder;
use memmap2::Mmap;
use std::fs::File;
use std::path::Path;
use ignore::WalkBuilder;
use rayon::prelude::*;
use std::sync::{Arc, Mutex};

use pyo3::types::PyModule;
use pyo3::Bound;

/// 파일에서 바이트 패턴을 검색하여 라인 번호와 내용을 반환합니다.
#[pyfunction]
fn search_file(path: &str, pattern: &str) -> PyResult<Vec<(usize, String)>> {
    do_search_file(path, pattern)
}

/// 실제 검색 로직 (내부 사용)
fn do_search_file(path: &str, pattern: &str) -> PyResult<Vec<(usize, String)>> {
    let file_path = Path::new(path);
    let file = match File::open(&file_path) {
        Ok(f) => f,
        Err(_) => return Ok(vec![]),
    };

    let mmap = match unsafe { Mmap::map(&file) } {
        Ok(m) => m,
        Err(_) => return Ok(vec![]),
    };

    let re = match RegexBuilder::new(pattern)
        .case_insensitive(true)
        .dot_matches_new_line(false)
        .build() {
            Ok(r) => r,
            Err(_) => return Ok(vec![]),
        };

    let mut results = Vec::new();
    let mut line_number = 1;

    for line_bytes in mmap.split(|&b| b == b'\n') {
        if re.is_match(line_bytes) {
            let line_cow = String::from_utf8_lossy(line_bytes);
            results.push((line_number, line_cow.trim().to_string()));
        }
        line_number += 1;
    }

    Ok(results)
}

/// 병렬 디렉토리 검색 함수
/// paths: 검색할 루트 디렉토리 리스트
/// pattern: 검색할 문자열 패턴 (Regex)
/// extensions: 검색할 파일 확장자 리스트 (옵션, 예: ["txt", "py"])
#[pyfunction]
#[pyo3(signature = (paths, pattern, extensions=None))]
fn search_dir(paths: Vec<String>, pattern: String, extensions: Option<Vec<String>>) -> PyResult<Vec<(String, usize, String)>> {
    // 1. 검색 결과 저장을 위한 Thread-safe 컨테이너
    let results = Arc::new(Mutex::new(Vec::new()));
    let pattern = Arc::new(pattern);
    
    // 확장자 필터 준비
    let exts = extensions.as_ref().map(|v| {
        v.iter().map(|s| s.trim_start_matches('.').to_lowercase()).collect::<Vec<_>>()
    });
    let exts = Arc::new(exts);

    // 2. 각 루트 경로에 대해 병렬 스캔 시작
    paths.par_iter().for_each(|root_path| {
        let walker = WalkBuilder::new(root_path)
            .hidden(false) // 숨김 파일 검색 여부 (true면 숨김, false면 보임)
            .ignore(false) // .gitignore 적용 여부
            .git_global(false)
            .git_ignore(false)
            .git_exclude(false)
            .threads(num_cpus::get()) // CPU 코어 수만큼 스레드 사용
            .build();

        let mut files = Vec::new();
        for result in walker {
            match result {
                Ok(entry) => {
                    if entry.file_type().map_or(false, |ft| ft.is_file()) {
                        let path = entry.path();
                        // 확장자 필터링
                        if let Some(ref valid_exts) = *exts {
                            if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                                if !valid_exts.contains(&ext.to_lowercase()) {
                                    continue;
                                }
                            } else {
                                continue; // 확장자가 없는 파일 스킵 (필터가 있을 때)
                            }
                        }
                        files.push(path.to_owned());
                    }
                }
                Err(_) => continue,
            }
        }

        // 수집된 파일들에 대해 병렬 내용 검색 수행
        files.par_iter().for_each(|file_path_buf| {
            let file_path_str = file_path_buf.to_string_lossy();
            if let Ok(file_results) = do_search_file(&file_path_str, &pattern) {
                if !file_results.is_empty() {
                    let mut lock = results.lock().unwrap();
                    for (line, content) in file_results {
                        lock.push((file_path_str.to_string(), line, content));
                    }
                }
            }
        });
    });

    let final_results = Arc::try_unwrap(results).unwrap().into_inner().unwrap();
    Ok(final_results)
}

/// 키워드가 포함된 파일 경로만 빠르게 검색하여 반환합니다. (Phase 3 Smart Scan)
/// GIL을 해제하여 UI 프리징을 방지하고, (경로, 파일크기) 튜플을 반환하여 Python 측 처리를 돕습니다.
#[pyfunction]
#[pyo3(signature = (paths, keyword, extensions=None))]
fn find_files_with_keyword(py: Python<'_>, paths: Vec<String>, keyword: String, extensions: Option<Vec<String>>) -> PyResult<Vec<(String, u64)>> {
    py.allow_threads(|| {
        let results = Arc::new(Mutex::new(Vec::new()));
        let keyword_bytes = keyword.as_bytes().to_vec();
        let keyword_bytes_arc = Arc::new(keyword_bytes);
        
        // 확장자 필터 준비
        let exts = extensions.as_ref().map(|v| {
            v.iter().map(|s| s.trim_start_matches('.').to_lowercase()).collect::<Vec<_>>()
        });
        let exts = Arc::new(exts);

        paths.par_iter().for_each(|root_path| {
            let walker = WalkBuilder::new(root_path)
                .hidden(false)
                .ignore(false)
                .git_global(false)
                .git_ignore(false)
                .git_exclude(false)
                .threads(num_cpus::get())
                .build();

            let mut files = Vec::new();
            for result in walker {
                match result {
                    Ok(entry) => {
                        if entry.file_type().map_or(false, |ft| ft.is_file()) {
                            let path = entry.path();
                            if let Some(ref valid_exts) = *exts {
                                if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                                    if !valid_exts.contains(&ext.to_lowercase()) {
                                        continue;
                                    }
                                } else {
                                    continue;
                                }
                            }
                            files.push(path.to_owned());
                        }
                    }
                    Err(_) => continue,
                }
            }

            files.par_iter().for_each(|file_path_buf| {
                let file_path = file_path_buf.as_path();
                // Binary Pre-check: 파일 열어서 mmap -> find bytes
                if let Ok(file) = File::open(file_path) {
                    if let Ok(mmap) = unsafe { Mmap::map(&file) } {
                        let re = RegexBuilder::new(&String::from_utf8_lossy(&keyword_bytes_arc))
                            .case_insensitive(true)
                            .dot_matches_new_line(false)
                            .build();

                        if let Ok(r) = re {
                            if r.is_match(&mmap) {
                                let mut lock = results.lock().unwrap();
                                // 파일 크기도 함께 저장
                                let size = file.metadata().map(|m| m.len()).unwrap_or(0);
                                lock.push((file_path.to_string_lossy().to_string(), size));
                            }
                        }
                    }
                }
            });
        });

        let final_results = Arc::try_unwrap(results).unwrap().into_inner().unwrap();
        Ok(final_results)
    })
}

/// A Python module implemented in Rust.
#[pymodule]
fn sf_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(search_file, m)?)?;
    m.add_function(wrap_pyfunction!(search_dir, m)?)?;
    m.add_function(wrap_pyfunction!(find_files_with_keyword, m)?)?;
    Ok(())
}
