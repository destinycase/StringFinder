//! 파일 스냅샷: 검색 중 파일이 바뀌어도 한 시점의 내용을 봅니다.
//! 8.1.5(엔진 1.0.1)에서 lib.rs에서 옮겼습니다. 함수 본문은 그대로입니다.

use super::*;

pub(crate) enum FileSnapshot {
    Mapped { _lock_file: File, mmap: Mmap },
    Owned(Vec<u8>),
}

impl FileSnapshot {
    pub(crate) fn as_slice(&self) -> &[u8] {
        match self {
            Self::Mapped { mmap, .. } => mmap,
            Self::Owned(bytes) => bytes,
        }
    }
}

pub(crate) fn metadata_matches(
    file: &File,
    expected_len: u64,
    expected_modified: Option<SystemTime>,
) -> bool {
    let Ok(meta) = file.metadata() else {
        return false;
    };
    if meta.len() != expected_len {
        return false;
    }
    expected_modified.is_none_or(|expected| meta.modified().ok() == Some(expected))
}

pub(crate) fn load_file_snapshot(
    mut file: File,
    expected_len: u64,
    expected_modified: Option<SystemTime>,
) -> Result<FileSnapshot, String> {
    if expected_len < 16 * 1024 {
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
        return Ok(FileSnapshot::Owned(bytes));
    }

    // A shared lock protects the mmap path on platforms that enforce file
    // sharing. If it cannot be acquired, use a read-copy snapshot instead.
    if fs2::FileExt::try_lock_shared(&file).is_ok() {
        match unsafe { Mmap::map(&file) } {
            Ok(mmap) if metadata_matches(&file, expected_len, expected_modified) => {
                return Ok(FileSnapshot::Mapped {
                    _lock_file: file,
                    mmap,
                });
            }
            Ok(_) | Err(_) => {}
        }
        let _ = fs2::FileExt::unlock(&file);
    }

    // 매핑하지 못한 큰 파일을 통째로 읽으면 메모리 예약 밖에서 파일 크기만큼 씁니다. 1GB를 넘으면 건너뜁니다(7.4.7).
    if expected_len > SNAPSHOT_COPY_MAX_BYTES {
        return Err(format!(
            "file is being used by another process or changed; not copied ({expected_len} bytes)"
        ));
    }
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    Ok(FileSnapshot::Owned(bytes))
}

/// 매핑하지 못했을 때 통째로 읽어도 되는 최대 크기입니다(공통 검색 크기 상한의 최댓값과 같음).
pub(crate) const SNAPSHOT_COPY_MAX_BYTES: u64 = 1024 * 1024 * 1024;
