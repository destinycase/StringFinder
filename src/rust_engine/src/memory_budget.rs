//! 큰 구조화 파일(JSON·XML·Excel·archive)을 동시에 너무 많이 열지 않게 하는 메모리 예산.
//! 8.1.5(엔진 1.0.1)에서 lib.rs에서 옮겼습니다. 함수 본문은 그대로입니다.

use super::*;

pub(crate) const LARGE_STRUCTURED_FILE_BYTES: u64 = 8 * 1024 * 1024;
/// UE 현지화 archive의 파일 크기 상한입니다. 검색 크기 상한 설정(최대 1GB)과 별도입니다(7.4.5).
pub(crate) const ARCHIVE_MAX_FILE_BYTES: u64 = 8 * 1024 * 1024 * 1024;
/// archive 검색이 파일 크기와 상관없이 쓰는 몫(뼈대·일치 항목)으로 예약하는 메모리입니다.
pub(crate) const ARCHIVE_FIXED_OVERHEAD: u64 = 64 * 1024 * 1024;

pub(crate) struct StructuredMemoryLimiter {
    capacity: u64,
    pub(crate) reserved: Mutex<u64>,
    available: Condvar,
}

pub(crate) struct StructuredMemoryPermit {
    limiter: Arc<StructuredMemoryLimiter>,
    reserved: u64,
}

pub(crate) enum StructuredPermitError {
    Cancelled,
    InsufficientBudget(u64),
}

impl StructuredMemoryLimiter {
    pub(crate) fn new(capacity: u64) -> Self {
        Self {
            capacity: capacity.max(1),
            reserved: Mutex::new(0),
            available: Condvar::new(),
        }
    }

    pub(crate) fn acquire(
        self: &Arc<Self>,
        requested: u64,
        stop_flag: &Arc<AtomicBool>,
    ) -> Result<StructuredMemoryPermit, StructuredPermitError> {
        if requested > self.capacity {
            return Err(StructuredPermitError::InsufficientBudget(requested));
        }
        let reservation = requested.max(1);
        let mut reserved = self
            .reserved
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        loop {
            if stop_flag.load(Ordering::Relaxed) {
                return Err(StructuredPermitError::Cancelled);
            }
            if reserved.saturating_add(reservation) <= self.capacity {
                *reserved += reservation;
                return Ok(StructuredMemoryPermit {
                    limiter: self.clone(),
                    reserved: reservation,
                });
            }
            let (next, _) = self
                .available
                .wait_timeout(
                    reserved,
                    std::time::Duration::from_millis(MONITOR_INTERVAL_MS),
                )
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            reserved = next;
        }
    }
}

impl Drop for StructuredMemoryPermit {
    fn drop(&mut self) {
        let mut reserved = self
            .limiter
            .reserved
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        *reserved = reserved.saturating_sub(self.reserved);
        self.limiter.available.notify_all();
    }
}

pub(crate) fn estimated_structured_memory(
    path: &Path,
    is_json: bool,
    is_archive: bool,
    is_xml: bool,
    is_excel: bool,
    selected: Option<&'static Encoding>,
) -> u64 {
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if is_archive && extension == "archive" {
        return estimated_archive_memory(path, selected);
    }
    let memory_profile =
        if is_excel || ["xlsx", "xlsm", "xlsb", "xls"].contains(&extension.as_str()) {
            Some((8, 128 * 1024 * 1024))
        } else if (is_json && extension == "json")
            || (is_xml && ["xml", "sf_xml"].contains(&extension.as_str()))
        {
            Some((5, 64 * 1024 * 1024))
        } else {
            None
        };
    let Some((multiplier, fixed_overhead)) = memory_profile else {
        return 0;
    };
    let file_size = std::fs::metadata(path).map(|meta| meta.len()).unwrap_or(0);
    if file_size < LARGE_STRUCTURED_FILE_BYTES {
        return 0;
    }
    if multiplier == 8 {
        return file_size
            .saturating_mul(multiplier)
            .saturating_add(fixed_overhead);
    }
    file_size
        .saturating_mul(multiplier)
        .saturating_add(1)
        .saturating_div(2)
        .saturating_add(fixed_overhead)
}

/// UTF-16 외 인코딩(CP949 등)은 UTF-8 사본과 변환 버퍼를 합쳐 파일 크기의 약 2.7배를 쓴다고 측정됐습니다(7.4.6 검수).
pub(crate) const ARCHIVE_LEGACY_MULTIPLIER: u64 = 3;

/// archive는 문서를 트리로 만들지 않으므로(7.4.4) UTF-8이면 고정 몫만 예약합니다. 다른 인코딩은 검색 전에
/// 파일 전체를 UTF-8로 바꾼 사본을 만들므로 그 몫을 더합니다. UTF-16은 1.5배, 그 밖의 인코딩은 3배입니다.
/// 인코딩은 실제 검색과 같은 순서로 정합니다(7.4.7): 사용자가 고른 인코딩, BOM과 앞부분의 UTF-16 판정,
/// 그다음 파일 전체가 올바른 UTF-8인지. 판정하지 못하면 큰 쪽(3배)으로 예약합니다.
pub(crate) fn estimated_archive_memory(path: &Path, selected: Option<&'static Encoding>) -> u64 {
    let Ok(file) = File::open(path) else {
        return 0;
    };
    let Ok(meta) = file.metadata() else {
        return 0;
    };
    let file_size = meta.len();
    if file_size < LARGE_STRUCTURED_FILE_BYTES {
        return 0;
    }
    let modified = meta.modified().ok();
    let copy_of = |multiplier: u64, divisor: u64| {
        file_size
            .saturating_mul(multiplier)
            .saturating_div(divisor)
            .saturating_add(ARCHIVE_FIXED_OVERHEAD)
    };
    let legacy = copy_of(ARCHIVE_LEGACY_MULTIPLIER, 1);
    let utf16 = copy_of(3, 2);
    let encoding = match selected {
        Some(encoding) => encoding,
        None => {
            // 파일 전체를 확인하려고 매핑합니다. 읽은 내용은 운영체제 캐시에 남아 바로 이은 검색이 다시 씁니다.
            // 검색 본문과 같이 공유 잠금을 얻고 크기·수정 시각이 그대로일 때만 매핑합니다(구현 정책 1.1, 7.4.8).
            // 그렇지 않으면 매핑하지 않고 큰 쪽으로 예약합니다.
            if fs2::FileExt::try_lock_shared(&file).is_err() {
                return legacy;
            }
            let Ok(mmap) = (unsafe { Mmap::map(&file) }) else {
                return legacy;
            };
            if !metadata_matches(&file, file_size, modified) {
                return legacy;
            }
            match crate::utils::detect_until_korean(&mmap) {
                crate::utils::Detection::Encoding(encoding) => encoding,
                crate::utils::Detection::KoreanCheck => return legacy,
            }
        }
    };
    if encoding == UTF_8 {
        ARCHIVE_FIXED_OVERHEAD
    } else if encoding == UTF_16LE || encoding == UTF_16BE {
        utf16
    } else {
        legacy
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn acquire_structured_permit(
    limiter: &Option<Arc<StructuredMemoryLimiter>>,
    path: &Path,
    is_json: bool,
    is_archive: bool,
    is_xml: bool,
    is_excel: bool,
    selected: Option<&'static Encoding>,
    stop_flag: &Arc<AtomicBool>,
) -> Result<Option<StructuredMemoryPermit>, StructuredPermitError> {
    let Some(limiter) = limiter else {
        return Ok(None);
    };
    let estimate =
        estimated_structured_memory(path, is_json, is_archive, is_xml, is_excel, selected);
    if estimate == 0 {
        return Ok(None);
    }
    limiter.acquire(estimate, stop_flag).map(Some)
}

#[cfg(test)]
mod archive_reservation_lock_tests {
    use super::*;
    use std::fs;

    /// 다른 핸들이 배타 잠금을 걸어 둔 archive는 매핑하지 않고 큰 쪽(3배)으로 예약합니다(7.4.8).
    #[test]
    fn locked_archive_is_not_mapped_for_reservation() {
        let root = std::env::temp_dir().join(format!(
            "sf-archive-lock-{}",
            SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("locked.archive");
        let size = 9 * 1024 * 1024usize;
        fs::write(&path, vec![b' '; size]).unwrap();
        assert_eq!(
            estimated_archive_memory(&path, None),
            ARCHIVE_FIXED_OVERHEAD
        );

        let holder = File::open(&path).unwrap();
        fs2::FileExt::lock_exclusive(&holder).unwrap();
        let locked = estimated_archive_memory(&path, None);
        fs2::FileExt::unlock(&holder).unwrap();
        drop(holder);
        if cfg!(windows) {
            assert_eq!(locked, size as u64 * 3 + ARCHIVE_FIXED_OVERHEAD);
        }
        fs::remove_dir_all(root).unwrap();
    }
}
