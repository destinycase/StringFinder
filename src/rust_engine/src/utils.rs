use encoding_rs::{Encoding, EUC_KR, UTF_16BE, UTF_16LE, UTF_8};
use simdutf8::basic::from_utf8 as simd_from_utf8;
use unicode_normalization::UnicodeNormalization;

use globset::{GlobSet, GlobSetBuilder};

pub fn detect_encoding(data: &[u8]) -> &'static Encoding {
    if data.len() >= 2 {
        if data.starts_with(b"\xff\xfe") {
            return UTF_16LE;
        }
        if data.starts_with(b"\xfe\xff") {
            return UTF_16BE;
        }
    }
    if data.starts_with(b"\xef\xbb\xbf") {
        return UTF_8;
    }

    // [Optimization] 파일 전체가 아닌 최대 64KB 샘플만 사용하여 인코딩 판별
    // 대용량 EUC-KR 파일에서 전체 디코딩 시도 시 발생하는 병목을 제거합니다.
    let sample_len = data.len().min(64 * 1024);
    let sample = &data[..sample_len];

    if simd_from_utf8(sample).is_ok() {
        return UTF_8;
    }

    // EUC-KR과 CP949 판별 (encoding_rs 기준 WINDOWS_949가 CP949/EUC-KR 호환)
    let (_res, _, has_error) = EUC_KR.decode(sample);
    if !has_error {
        return EUC_KR;
    }
    
    // 기본값으로 UTF-8 반환
    UTF_8
}

pub fn decode_bytes(bytes: &[u8], encoding: &'static Encoding) -> String {
    let (res, _, _) = encoding.decode(bytes);
    res.into_owned()
}

// get_line_number: 코드에서 미사용 -> clippy 정리를 위해 제거
// 필요 시 lib.rs의 do_search_with_mmap나 search_file_internal에서 직접 코드 삽입

pub fn parse_search_mode(mode_bits: Option<u32>) -> (bool, bool, bool, bool, bool, bool, bool) {
    let bits = mode_bits.unwrap_or(crate::types::MODE_NORMAL);
    (
        (bits & crate::types::MODE_JSON) != 0,
        (bits & crate::types::MODE_XML) != 0,
        (bits & crate::types::MODE_ARCHIVE) != 0,
        (bits & crate::types::MODE_EXACT) != 0,
        (bits & crate::types::MODE_EXCEL) != 0,
        (bits & crate::types::MODE_EXCLUDE_BINARY) != 0,
        (bits & crate::types::MODE_BOOLEAN_ONLY) != 0,
    )
}

pub fn build_glob_set(filters: &[String]) -> Option<GlobSet> {
    if filters.is_empty() {
        return None;
    }
    let mut builder = GlobSetBuilder::new();
    for filter in filters {
        let pattern = if !filter.contains('*') && !filter.contains('?') {
            format!("*{}*", filter)
        } else {
            filter.clone()
        };
        // [Optimization] GlobBuilder를 사용하여 대소문자 무시 속성을 직접 부여 (힙 할당 감소)
        if let Ok(glob) = globset::GlobBuilder::new(&pattern)
            .case_insensitive(true)
            .build() 
        {
            builder.add(glob);
        }
    }
    builder.build().ok()
}

pub fn match_filename_glob(filename: &str, glob_set: &Option<GlobSet>) -> bool {
    match glob_set {
        // [Optimization] GlobSetBuilder에서 case_insensitive(true)를 설정했으므로
        // 여기서 더 이상 filename.to_lowercase()를 호출할 필요가 없음 (할당 제거)
        Some(set) => set.is_match(filename),
        None => true,
    }
}

pub fn generate_search_patterns(
    keyword: &str,
    is_xml: bool,
    is_json: bool,
    is_archive: bool,
) -> Vec<String> {
    let mut patterns = Vec::new();
    patterns.push(keyword.to_string()); // Raw match

    if is_xml || is_json || is_archive {
        if is_xml {
            let mut encoded = String::new();
            for c in keyword.chars() {
                let mut buf = [0; 4];
                let s = c.encode_utf8(&mut buf);
                if s == "<" {
                    encoded.push_str("&lt;");
                } else if s == ">" {
                    encoded.push_str("&gt;");
                } else if s == "&" {
                    encoded.push_str("&amp;");
                } else if s == "\"" {
                    encoded.push_str("&quot;");
                } else if s == "'" {
                    encoded.push_str("&apos;");
                } else if c.is_ascii() {
                    encoded.push_str(s);
                } else {
                    encoded.push_str(&format!("&#{};", c as u32));
                }
            }
            if encoded != keyword {
                patterns.push(encoded);
            }
        }

        if is_json || is_archive {
            let mut encoded = String::new();
            for c in keyword.chars() {
                if c.is_ascii() {
                    encoded.push(c);
                } else {
                    encoded.push_str(&format!("\\u{:04X}", c as u32));
                }
            }
            if encoded != keyword {
                patterns.push(encoded);
            }
        }
    }
    patterns
}
pub fn is_binary(data: &[u8]) -> bool {
    if data.len() >= 2 && (data.starts_with(b"\xff\xfe") || data.starts_with(b"\xfe\xff")) {
        return false;
    }
    if data.len() >= 3 && data.starts_with(b"\xef\xbb\xbf") {
        return false;
    }

    let check_len = data.len().min(1024);
    data[..check_len].contains(&0)
}

// regex::Regex, std::sync::OnceLock 미사용 import 제거 (clippy)

pub fn normalize_unicode(text: &str) -> String {
    text.nfc().collect::<String>()
}

pub fn find_bytes(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    memchr::memmem::find(haystack, needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_search_mode_none_defaults_to_all_false() {
        let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary, is_boolean) =
            parse_search_mode(None);

        assert!(!is_json);
        assert!(!is_xml);
        assert!(!is_archive);
        assert!(!is_exact);
        assert!(!is_excel);
        assert!(!exclude_binary);
        assert!(!is_boolean);
    }

    #[test]
    fn parse_search_mode_mixed_flags_are_decoded_correctly() {
        let bits =
            crate::types::MODE_JSON | crate::types::MODE_EXACT | crate::types::MODE_EXCLUDE_BINARY;
        let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary, is_boolean) =
            parse_search_mode(Some(bits));

        assert!(is_json);
        assert!(!is_xml);
        assert!(!is_archive);
        assert!(is_exact);
        assert!(!is_excel);
        assert!(exclude_binary);
        assert!(!is_boolean);
    }

    #[test]
    fn generate_search_patterns_includes_raw_xml_and_json_variants() {
        let keyword = "A&B한";
        let patterns = generate_search_patterns(keyword, true, true, false);

        assert!(patterns.iter().any(|p| p == keyword));
        assert!(patterns.iter().any(|p| p == "A&amp;B&#54620;"));
        assert!(patterns.iter().any(|p| p == r"A&B\uD55C"));
    }

    #[test]
    fn is_binary_detects_nul_but_respects_text_bom() {
        assert!(!is_binary(&[0xEF, 0xBB, 0xBF, b'h', b'i']));
        assert!(!is_binary(&[0xFF, 0xFE, b'h', 0x00]));
        assert!(is_binary(&[0x00, b'h', b'i']));
    }
}
