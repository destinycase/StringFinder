use pyo3::prelude::*;
use regex::bytes::RegexBuilder;
use memmap2::Mmap;
use std::fs::File;
use std::path::{Path, PathBuf};
use ignore::WalkBuilder;
use rayon::prelude::*;
use std::collections::HashSet; // 확장자 검색 속도 향상용

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
        Err(e) => return Err(PyErr::new::<pyo3::exceptions::PyIOError, _>(
            format!("Failed to open file {}: {}", path, e)
        )),
    };

    // 0바이트 파일 처리
    if let Ok(meta) = file.metadata() {
        if meta.len() == 0 {
            return Ok(Vec::new());
        }
    }

    let mmap = match unsafe { Mmap::map(&file) } {
        Ok(m) => m,
        Err(e) => return Err(PyErr::new::<pyo3::exceptions::PyIOError, _>(
            format!("Failed to mmap file {}: {}", path, e)
        )),
    };

    let re = match RegexBuilder::new(pattern)
        .case_insensitive(true)
        .dot_matches_new_line(false)
        .build() {
            Ok(r) => r,
            Err(e) => return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                format!("Invalid regex pattern: {}", e)
            )),
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
    // 1. 공유 데이터 준비 (Arc 불필요, Rayon이 알아서 분배하지만 명시적으로 클론 비용 절약)
    // 패턴은 각 스레드에서 정규식 컴파일에 쓰임
    
    // 확장자 필터: HashSet으로 변환하여 조회 속도 O(1)로 최적화
    let exts = extensions.map(|v| {
        v.iter()
            .map(|s| s.trim_start_matches('.').to_lowercase())
            .collect::<HashSet<_>>()
    });
    
    // 2. 모든 루트 경로에서 파일 목록을 먼저 수집 (병렬화 가능하지만 여기서는 단순화)
    // Rayon의 par_iter를 사용하여 루트별로 병렬 수집 후 합침
    let all_files: Vec<PathBuf> = paths.par_iter().flat_map(|root_path| {
        let walker = WalkBuilder::new(root_path)
            .hidden(false)
            .ignore(false)
            .git_global(false)
            .git_ignore(false)
            .git_exclude(false)
            .threads(num_cpus::get()) // WalkBuilder 내부 스레딩 활용
            .build();

        walker.filter_map(|entry| {
            match entry {
                Ok(e) => {
                    if e.file_type().map_or(false, |ft| ft.is_file()) {
                        let path = e.path();
                         if let Some(ref valid_exts) = exts {
                            if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                                if !valid_exts.contains(&ext.to_lowercase()) {
                                    return None;
                                }
                            } else {
                                return None; // 확장자 없는 파일 스킵 (필터 존재 시)
                            }
                        }
                        Some(path.to_owned())
                    } else {
                        None
                    }
                },
                Err(_) => None,
            }
        }).collect::<Vec<_>>()
    }).collect();

    // 3. 수집된 파일들에 대해 Lock-Free 병렬 검색 수행
    // flat_map + collect 패턴 사용: 각 스레드가 로컬 벡터를 만들고 최종적으로 병합됨 (No Mutex)
    let results: Vec<(String, usize, String)> = all_files.par_iter().flat_map(|path_buf| {
        let path_str = path_buf.to_string_lossy();
        
        match do_search_file(&path_str, &pattern) {
            Ok(file_results) => {
                // (line, content) -> (path, line, content) 변환 매핑
                // 여기서 문자열 복사가 일어나지만, 결과가 있는 경우뿐이므로 비용 감수 가능
                file_results.into_iter().map(|(line, content)| {
                    (path_str.to_string(), line, content)
                }).collect::<Vec<_>>()
            },
            Err(_) => Vec::new(), // 검색 실패 시 무시 (빈 벡터)
        }
    }).collect();

    Ok(results)
}

/// 키워드가 포함된 파일 경로만 빠르게 검색하여 반환합니다. (Phase 3 Smart Scan)
/// GIL을 해제하여 UI 프리징을 방지하고, (경로, 파일크기) 튜플을 반환하여 Python 측 처리를 돕습니다.
#[pyfunction]
#[pyo3(signature = (paths, keyword, extensions=None))]
fn find_files_with_keyword(py: Python<'_>, paths: Vec<String>, keyword: String, extensions: Option<Vec<String>>) -> PyResult<Vec<(String, u64)>> {
    py.allow_threads(|| {
        let keyword_bytes = keyword.as_bytes().to_vec();
        
        let exts = extensions.map(|v| {
            v.iter()
                .map(|s| s.trim_start_matches('.').to_lowercase())
                .collect::<HashSet<_>>()
        });

        // 1. 파일 수집
        let all_files: Vec<PathBuf> = paths.par_iter().flat_map(|root_path| {
            let walker = WalkBuilder::new(root_path)
                .hidden(false).ignore(false).git_global(false).git_ignore(false).git_exclude(false)
                .threads(num_cpus::get())
                .build();

            walker.filter_map(|entry| {
                match entry {
                    Ok(e) => {
                        if e.file_type().map_or(false, |ft| ft.is_file()) {
                            let path = e.path();
                             if let Some(ref valid_exts) = exts {
                                if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                                    if !valid_exts.contains(&ext.to_lowercase()) {
                                        return None;
                                    }
                                } else {
                                    return None;
                                }
                            }
                            Some(path.to_owned())
                        } else {
                            None
                        }
                    },
                    Err(_) => None,
                }
            }).collect::<Vec<_>>()
        }).collect();

        // 2. 병렬 검색 (Lock-Free)
        let results: Vec<(String, u64)> = all_files.par_iter().flat_map(|path_buf| {
             let path = path_buf.as_path();
             // Binary Pre-check
             if let Ok(file) = File::open(path) {
                 // 0바이트 체크
                if let Ok(meta) = file.metadata() {
                    if meta.len() == 0 { return None; }
                }

                if let Ok(mmap) = unsafe { Mmap::map(&file) } {
                     // 단순 바이트 매칭 (Regex 오버헤드 없이)
                     // 하지만 대소문자 구분을 위해 RegexBuilder 사용이 안전함 (TODO: Boyer-Moore 등으로 최적화 가능)
                     let re = RegexBuilder::new(&String::from_utf8_lossy(&keyword_bytes))
                            .case_insensitive(true)
                            .dot_matches_new_line(false)
                            .build();

                    if let Ok(r) = re {
                        if r.is_match(&mmap) {
                            let size = file.metadata().map(|m| m.len()).unwrap_or(0);
                            return Some((path.to_string_lossy().to_string(), size));
                        }
                    }
                }
             }
             None
        }).collect();

        Ok(results)
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
