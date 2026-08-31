use encoding_rs::{Encoding, EUC_KR, UTF_16BE, UTF_16LE, UTF_8};
use std::collections::HashSet;
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

    // 성능 최적화: 파일 전체가 아닌 최대 64KB 샘플만 사용하여 인코딩을 판별합니다.
    let sample_len = data.len().min(64 * 1024);
    let sample = &data[..sample_len];

    // No-BOM UTF-16 휴리스틱 감지 (Python의 detect_encoding_quickly와 동기화)
    // 짝수/홀수 오프셋의 NUL 바이트 분포를 분석하여 UTF-16LE/BE 여부를 추정합니다.
    if sample.len() >= 4 {
        let mut zero_even = 0;
        let mut zero_odd = 0;
        let check_limit = (sample.len() / 2) * 2;
        for i in (0..check_limit).step_by(2) {
            if sample[i] == 0 { zero_even += 1; }
            if sample[i + 1] == 0 { zero_odd += 1; }
        }
        let half = (check_limit / 2) as f32;
        // 특정 오프셋에 NUL 바이트가 70% 이상 집중되어 있으면 UTF-16으로 간주
        if zero_odd as f32 > half * 0.7 && zero_even <= (half * 0.1) as usize {
            return UTF_16LE;
        }
        if zero_even as f32 > half * 0.7 && zero_odd <= (half * 0.1) as usize {
            return UTF_16BE;
        }
    }

    // B3: EUC-KR 오탐 방지 — 고바이트(0x80↑)가 없으면 ASCII-only 파일이므로 UTF-8 반환합니다.
    // 한국어 EUC-KR 파일은 반드시 고바이트를 포함하므로 기존 동작에 영향 없습니다.
    // UTF-16 ASCII 데이터는 NUL 바이트가 포함되어도 UTF-8로 형식상 해석될 수
    // 있으므로, UTF-16 휴리스틱을 먼저 적용한 뒤 UTF-8 판정을 수행합니다.
    if simd_from_utf8(sample).is_ok() {
        return UTF_8;
    }

    let has_high_bytes = sample.iter().any(|&b| b >= 0x80);
    if !has_high_bytes {
        return UTF_8;
    }
    let (_res, _, has_error) = EUC_KR.decode(sample);
    if !has_error { return EUC_KR; }
    
    // 기본값으로 UTF-8 반환
    UTF_8
}

pub fn decode_bytes(bytes: &[u8], encoding: &'static Encoding) -> String {
    let (res, _, _) = encoding.decode(bytes);
    res.into_owned()
}

// get_line_number: 코드에서 미사용 -> clippy 정리를 위해 제거
// 필요 시 lib.rs의 do_search_with_mmap나 search_file_internal에서 직접 코드 삽입

pub fn parse_search_mode(mode_bits: Option<u32>) -> (bool, bool, bool, bool, bool, bool) {
    let bits = mode_bits.unwrap_or(crate::types::MODE_NORMAL);
    (
        (bits & crate::types::MODE_JSON) != 0,
        (bits & crate::types::MODE_XML) != 0,
        (bits & crate::types::MODE_EXACT) != 0,
        (bits & crate::types::MODE_EXCEL) != 0,
        (bits & crate::types::MODE_EXCLUDE_BINARY) != 0,
        (bits & crate::types::MODE_EXISTENCE_ONLY) != 0,
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
        // GlobBuilder를 사용하여 대소문자 무시 속성을 직접 부여하여 힙 할당을 줄입니다.
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
        // GlobSetBuilder에서 case_insensitive(true)를 설정했으므로 추가적인 소문자 변환이 필요 없습니다.
        // 여기서 더 이상 filename.to_lowercase()를 호출할 필요가 없음 (할당 제거)
        Some(set) => set.is_match(filename),
        None => true,
    }
}

pub fn generate_search_patterns(
    keyword: &str,
    is_xml: bool,
    is_json: bool,
) -> Vec<String> {
    let mut patterns = HashSet::new();
    patterns.insert(keyword.to_string());
    
    // Unicode Casefold 정합성을 위해 다양한 변형을 추가하여 검색 누락을 방지합니다.
    let lower = keyword.to_lowercase();
    let upper = keyword.to_uppercase();
    let lower_upper = lower.to_uppercase();
    
    patterns.insert(lower);
    patterns.insert(upper);
    patterns.insert(lower_upper);

    if is_xml || is_json {
        let mut variants = Vec::new();
        for p in patterns.iter() {
            if is_xml {
                let mut encoded = String::new();
                for c in p.chars() {
                    let mut buf = [0; 4];
                    let s = c.encode_utf8(&mut buf);
                    if s == "<" { encoded.push_str("&lt;"); }
                    else if s == ">" { encoded.push_str("&gt;"); }
                    else if s == "&" { encoded.push_str("&amp;"); }
                    else if s == "\"" { encoded.push_str("&quot;"); }
                    else if s == "'" { encoded.push_str("&apos;"); }
                    else if c.is_ascii() { encoded.push_str(s); }
                    else { encoded.push_str(&format!("&#{};", c as u32)); }
                }
                if encoded != *p { variants.push(encoded); }
            }
            if is_json {
                let mut encoded_lower = String::new();
                let mut encoded_upper = String::new();
                for c in p.chars() {
                    if c.is_ascii() {
                        encoded_lower.push(c);
                        encoded_upper.push(c);
                    }
                    else {
                        // 대문자(\uXXXX)와 소문자(\uxxxx) 양쪽 모두 추가합니다.
                        push_json_unicode_escape(&mut encoded_lower, c, false);
                        push_json_unicode_escape(&mut encoded_upper, c, true);
                        // encoded는 소문자 패턴으로 기본 빌드
                    }
                }
                if encoded_lower != *p { variants.push(encoded_lower.clone()); }
                if encoded_upper != *p && encoded_upper != encoded_lower {
                    variants.push(encoded_upper);
                }
            }
        }
        for v in variants {
            patterns.insert(v);
        }
    }
    patterns.into_iter().collect()
}

fn push_json_unicode_escape(output: &mut String, value: char, uppercase: bool) {
    use std::fmt::Write;

    let code_point = value as u32;
    if code_point <= 0xFFFF {
        if uppercase {
            write!(output, "\\u{:04X}", code_point).unwrap();
        } else {
            write!(output, "\\u{:04x}", code_point).unwrap();
        }
        return;
    }

    let adjusted = code_point - 0x1_0000;
    let high = 0xD800 + (adjusted >> 10);
    let low = 0xDC00 + (adjusted & 0x3FF);
    if uppercase {
        write!(output, "\\u{:04X}\\u{:04X}", high, low).unwrap();
    } else {
        write!(output, "\\u{:04x}\\u{:04x}", high, low).unwrap();
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_search_mode_none_defaults_to_all_false() {
        let (is_json, is_xml, is_exact, is_excel, exclude_binary, existence_only) =
            parse_search_mode(None);

        assert!(!is_json);
        assert!(!is_xml);
        assert!(!is_exact);
        assert!(!is_excel);
        assert!(!exclude_binary);
        assert!(!existence_only);
    }

    #[test]
    fn parse_search_mode_mixed_flags_are_decoded_correctly() {
        let bits =
            crate::types::MODE_JSON | crate::types::MODE_EXACT | crate::types::MODE_EXCLUDE_BINARY;
        let (is_json, is_xml, is_exact, is_excel, exclude_binary, existence_only) =
            parse_search_mode(Some(bits));

        assert!(is_json);
        assert!(!is_xml);
        assert!(is_exact);
        assert!(!is_excel);
        assert!(exclude_binary);
        assert!(!existence_only);
    }

    #[test]
    fn generate_search_patterns_includes_raw_xml_and_json_variants() {
        let keyword = "A&B한";
        let patterns = generate_search_patterns(keyword, true, true);

        assert!(patterns.iter().any(|p| p == keyword));
        assert!(patterns.iter().any(|p| p == "A&amp;B&#54620;"));
        assert!(patterns.iter().any(|p| p == r"A&B\ud55c"));
    }

    #[test]
    fn json_unicode_variants_encode_the_whole_keyword_only() {
        let patterns = generate_search_patterns("한국어", false, true);

        assert!(patterns.iter().any(|p| p == r"\ud55c\uad6d\uc5b4"));
        assert!(!patterns.iter().any(|p| p == r"\ud55c"));
        assert!(!patterns.iter().any(|p| p == r"\uad6d"));
        assert!(!patterns.iter().any(|p| p == r"\uc5b4"));
    }

    #[test]
    fn json_unicode_variants_use_surrogate_pairs_for_non_bmp_characters() {
        let patterns = generate_search_patterns("😀", false, true);

        assert!(patterns.iter().any(|p| p == r"\ud83d\ude00"));
        assert!(!patterns.iter().any(|p| p == r"\u1f600"));
    }

    #[test]
    fn is_binary_detects_nul_but_respects_text_bom() {
        assert!(!is_binary(&[0xEF, 0xBB, 0xBF, b'h', b'i']));
        assert!(!is_binary(&[0xFF, 0xFE, b'h', 0x00]));
        assert!(is_binary(&[0x00, b'h', b'i']));
    }
}
