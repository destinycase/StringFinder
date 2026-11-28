//! 폴더 순회 보조: 휴지통 제외, 정션, 크기 확인, 중복 방문 방지, 순회 오류 기록.
//! 8.1.5(엔진 1.0.1)에서 lib.rs에서 옮겼습니다. 함수 본문은 그대로입니다.

use super::*;

pub(crate) fn has_recycle_bin_component(path: &Path) -> bool {
    path.components().any(|component| {
        component
            .as_os_str()
            .to_str()
            .is_some_and(|value| value.eq_ignore_ascii_case("$RECYCLE.BIN"))
    })
}

pub(crate) fn is_direct_child_of_filesystem_root(path: &Path) -> bool {
    path.is_absolute()
        && path
            .components()
            .filter(|component| matches!(component, std::path::Component::Normal(_)))
            .count()
            == 1
}

pub(crate) fn should_exclude_recycle_path(path: &Path) -> bool {
    if has_recycle_bin_component(path) {
        return true;
    }

    let Some(parent) = path.parent() else {
        return false;
    };
    if !is_direct_child_of_filesystem_root(parent) {
        return false;
    }
    let Some(root) = parent.parent() else {
        return false;
    };
    let Some(alias_name) = parent.file_name() else {
        return false;
    };
    if !root.join("$RECYCLE.BIN").join(alias_name).is_dir() {
        return false;
    }

    // Anti-ransomware canaries can expose a root-level directory alias while
    // the final file path lives below $RECYCLE.BIN. The cheap sibling-directory
    // check above ensures canonicalization is limited to alias candidates.
    std::fs::canonicalize(path)
        .ok()
        .is_some_and(|resolved| has_recycle_bin_component(&resolved))
}

pub(crate) fn walk_error_entries(error: &ignore::Error) -> SkippedEntries {
    // Only error handling allocates these records. Do not parse Display text
    // for paths: wrappers and aggregate errors have a structured path already.
    static UNKNOWN_ID: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let mut entries = Vec::new();
    let mut pending = vec![(error, None::<&Path>)];
    while let Some((current, path)) = pending.pop() {
        match current {
            ignore::Error::WithPath { path, err } => pending.push((err, Some(path))),
            ignore::Error::WithDepth { err, .. } | ignore::Error::WithLineNumber { err, .. } => {
                pending.push((err, path))
            }
            ignore::Error::Partial(errors) if !errors.is_empty() => {
                pending.extend(errors.iter().rev().map(|err| (err, path)));
            }
            _ => {
                let path = match current {
                    ignore::Error::Loop { child, .. } => Some(child.as_path()),
                    _ => path,
                };
                let identifier = path
                    .map(|path| path.to_string_lossy().into_owned())
                    .unwrap_or_else(|| {
                        format!(
                            "__SF_WALK_UNKNOWN__|{}",
                            UNKNOWN_ID.fetch_add(1, Ordering::Relaxed)
                        )
                    });
                let detail = match path {
                    Some(path) => format!("{}: {}", path.display(), current),
                    None => current.to_string(),
                };
                entries.push((identifier, encode_skip_reason(REASON_ERR_WALK, detail)));
            }
        }
    }
    entries
}

pub(crate) fn search_walk_builder(
    root_path: &Path,
    exclude_hidden: bool,
    include_junctions: bool,
) -> WalkBuilder {
    let mut builder = WalkBuilder::new(root_path);
    builder.hidden(exclude_hidden);
    builder.follow_links(include_junctions);
    // Search results must not depend on repository ignore rules; the
    // precise-search scanner also traverses files listed in .gitignore/.ignore.
    builder.ignore(false);
    builder.git_ignore(false);
    builder.git_exclude(false);
    builder.git_global(false);
    // 검색 대상이 파일 하나이면 순회 스레드를 여러 개 띄울 필요가 없습니다(띄우는 비용만 듦).
    // 폴더는 라이브러리 기본값(CPU 수, 최대 12개)을 씁니다. 이 장비(논리 CPU 20개)에서 20개로
    // 늘려 봤지만 작은 파일이 많은 표본(A)에서 이득이 없고 조금 느려져 기본값을 유지합니다.
    if root_path.is_file() {
        builder.threads(1);
    }
    builder
}

/// 파일 크기 상한입니다. UE 현지화 archive는 문서 전체를 메모리에 쌓지 않고 읽으므로(7.4.4)
/// 검색 크기 상한 설정과 별도로 8GB까지 검색합니다(7.4.5).
pub(crate) fn file_size_limit_for(path: &Path, is_archive: bool, common_limit: u64) -> u64 {
    let archive_document = is_archive
        && path
            .extension()
            .and_then(|ext| ext.to_str())
            .is_some_and(|ext| ext.eq_ignore_ascii_case("archive"));
    if archive_document {
        ARCHIVE_MAX_FILE_BYTES
    } else {
        common_limit
    }
}

pub(crate) fn size_check_metadata(
    entry: &ignore::DirEntry,
    fresh: bool,
    limit: u64,
) -> Option<std::fs::Metadata> {
    if fresh {
        return std::fs::metadata(entry.path()).ok();
    }
    entry.metadata().ok().and_then(|metadata| {
        if metadata.len() > limit {
            // A formerly large file may have shrunk since enumeration.
            std::fs::metadata(entry.path()).ok()
        } else {
            Some(metadata)
        }
    })
}

/// Distinguish Windows mount-point junctions from symbolic links. Only linked
/// directories need the reparse query; ordinary files retain the cheap path.
pub(crate) fn is_junction(path: &Path) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
        use std::os::windows::io::AsRawHandle;
        let Ok(metadata) = std::fs::symlink_metadata(path) else {
            return false;
        };
        if metadata.file_attributes() & 0x400 == 0 {
            return false;
        }
        let Ok(file) = std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(0x02000000 | 0x00200000)
            .open(path)
        else {
            return false;
        };
        #[link(name = "kernel32")]
        extern "system" {
            fn DeviceIoControl(
                handle: *mut std::ffi::c_void,
                code: u32,
                input: *mut std::ffi::c_void,
                input_size: u32,
                output: *mut std::ffi::c_void,
                output_size: u32,
                returned: *mut u32,
                overlapped: *mut std::ffi::c_void,
            ) -> i32;
        }
        let mut buffer = [0u8; 16384];
        let mut returned = 0;
        // The handle and output buffer remain alive throughout this synchronous call.
        let ok = unsafe {
            DeviceIoControl(
                file.as_raw_handle(),
                0x000900A8,
                std::ptr::null_mut(),
                0,
                buffer.as_mut_ptr().cast(),
                buffer.len() as u32,
                &mut returned,
                std::ptr::null_mut(),
            )
        };
        ok != 0
            && returned >= 4
            && u32::from_le_bytes(buffer[..4].try_into().unwrap()) == 0xA0000003
    }
    #[cfg(not(windows))]
    {
        let _ = path;
        false
    }
}

pub(crate) fn claim_walk_entry(
    path: &Path,
    include_junctions: bool,
    visited: &Mutex<std::collections::HashSet<std::path::PathBuf>>,
) -> bool {
    let junction = is_junction(path);
    if (!include_junctions && junction) || (path.is_symlink() && !junction) {
        return false;
    }
    if include_junctions {
        // Atomic insertion is shared by all parallel walkers and all roots.
        let resolved = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
        return visited
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(resolved);
    }
    true
}
