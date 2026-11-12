//! 모든 검색 형식이 함께 쓰는 비교 기준입니다.
//!
//! 검색어와 본문(또는 값)을 같은 함수 `key(x) = NFC(casefold(NFC(x)))`로 바꾼 뒤
//! 비교합니다. 대소문자 접기는 Unicode 전체 접기(C+F, 터키어 T 제외)이고,
//! 접기 뒤 NFC를 다시 적용해 정규형 무시 비교(Unicode D145)와 같은 결과를 냅니다.
//!
//! 대부분의 본문은 변환해도 바뀌지 않으므로 다음 빠른 경로를 씁니다.
//! - ASCII 문자열: `key`는 ASCII 소문자화와 같습니다.
//! - "불변 글자"(변환해도 자기 자신이고 앞 글자와 합쳐지지 않는 글자, 예: 한글 음절)와
//!   ASCII만 있는 문자열: `key`는 ASCII 소문자화와 같습니다.
//! - 검색어의 `key`가 ASCII일 때: ASCII 일치에 영향을 줄 수 있는 글자(`key`가 ASCII를
//!   만들거나 ASCII 글자와 합쳐지는 글자)가 없으면 원본에서 ASCII 대소문자 무시로 찾아도
//!   결과가 같습니다.
//!
//! CR·LF는 어떤 글자와도 합쳐지지 않고 `key`로 새로 생기지도 않으므로, 줄 단위 변환이
//! 파일 전체 변환과 같습니다. 그래서 변환이 필요한 줄만 변환합니다.

use std::sync::OnceLock;

use aho_corasick::{AhoCorasick, AhoCorasickBuilder, MatchKind};
use unicode_normalization::char::canonical_combining_class;
use unicode_normalization::{is_nfc_quick, IsNormalized, UnicodeNormalization};

use crate::casefold_table::{CASEFOLD, CASEFOLD_UNICODE_VERSION};

/// 엔진이 비교에 쓰는 Unicode 버전입니다. 정규화 데이터와 대소문자 접기 데이터는 같은 버전이어야 합니다.
pub const UNICODE_VERSION: (u8, u8, u8) = unicode_normalization::UNICODE_VERSION;

const _: () = {
    // 두 데이터의 버전이 다르면 새로 추가된 글자에서 비교가 어긋나므로 빌드를 막습니다.
    assert!(UNICODE_VERSION.0 == CASEFOLD_UNICODE_VERSION.0);
    assert!(UNICODE_VERSION.1 == CASEFOLD_UNICODE_VERSION.1);
    assert!(UNICODE_VERSION.2 == CASEFOLD_UNICODE_VERSION.2);
};

fn fold_char(c: char) -> Option<&'static str> {
    let code = c as u32;
    CASEFOLD
        .binary_search_by_key(&code, |entry| entry.0)
        .ok()
        .map(|index| CASEFOLD[index].1)
}

/// Unicode 전체 대소문자 접기(C+F)입니다.
pub fn casefold(text: &str) -> String {
    let mut output = String::with_capacity(text.len());
    for c in text.chars() {
        match fold_char(c) {
            Some(folded) => output.push_str(folded),
            None => output.push(c),
        }
    }
    output
}

/// 정식 비교 함수 `NFC(casefold(NFC(x)))`입니다. 빠른 경로 없이 정의 그대로 계산합니다.
pub fn key_reference(text: &str) -> String {
    let nfc: String = text.nfc().collect();
    casefold(&nfc).nfc().collect()
}

const CLASS_INVARIANT: u8 = 1;
/// 대소문자 접기 결과가 불변 글자·ASCII로만 되어 있고, 자기도 앞 글자와 합쳐지지 않는 글자입니다
/// (예: 키릴·그리스 대문자, `É`, `ß`→`ss`). `key`에서 접기 결과로 바로 바꿀 수 있습니다.
const CLASS_SIMPLE: u8 = 4;

/// 불변 글자인지(분류표 계산 중에도 쓸 수 있도록 표 없이) 판정합니다.
fn invariant_by_definition(c: char) -> bool {
    canonical_combining_class(c) == 0
        && is_nfc_quick(std::iter::once(c)) == IsNormalized::Yes
        && fold_char(c).is_none()
}

/// 모든 코드포인트의 분류표(첫 사용 때 한 번 계산)입니다.
/// 분류표가 덮는 범위입니다. 이보다 큰 코드포인트(U+30000 이상)는 채택한 Unicode 버전에서 모두
/// 불변 글자입니다(결합 등급 0, NFC_QC=Yes, 대소문자 접기 없음). 테스트
/// `scalars_beyond_the_class_table_are_invariant`가 모든 값을 확인합니다. 표를 줄여 첫 검색 때
/// 드는 계산 시간을 줄입니다.
const CLASS_TABLE_LIMIT: u32 = 0x3_0000;

/// 코드포인트의 분류입니다(표 밖은 불변).
#[inline]
fn class_code(code: u32) -> u8 {
    let table = classes();
    if code < CLASS_TABLE_LIMIT {
        table[code as usize]
    } else {
        CLASS_INVARIANT
    }
}

fn classes() -> &'static [u8] {
    static TABLE: OnceLock<Vec<u8>> = OnceLock::new();
    TABLE.get_or_init(|| {
        let mut table = vec![0u8; CLASS_TABLE_LIMIT as usize];
        for code in 0x80u32..CLASS_TABLE_LIMIT {
            let Some(c) = char::from_u32(code) else {
                continue;
            };
            let starter = canonical_combining_class(c) == 0;
            let quick_yes = is_nfc_quick(std::iter::once(c)) == IsNormalized::Yes;
            let folded = fold_char(c);
            if starter && quick_yes && folded.is_none() {
                // 변환해도 자기 자신이고, 앞 글자와 합쳐지지도 않습니다(NFC_QC=Maybe 아님).
                table[code as usize] = CLASS_INVARIANT;
                continue;
            }
            let simple = starter
                && quick_yes
                && folded.is_some_and(|text| {
                    text.chars()
                        .all(|part| part.is_ascii() || invariant_by_definition(part))
                });
            if simple {
                table[code as usize] = CLASS_SIMPLE;
            }
        }
        table
    })
}

/// ASCII 검색어의 일치 여부를 바꿀 수 있는 글자표입니다. 계산이 비싸고(결합 부호마다 ASCII 글자와
/// 짝지어 정의대로 계산) 검색의 주 경로에서는 쓰지 않으므로, 처음 필요할 때 따로 계산합니다.
fn affects_table() -> &'static [bool] {
    static TABLE: OnceLock<Vec<bool>> = OnceLock::new();
    TABLE.get_or_init(|| {
        let classes = classes();
        let mut table = vec![false; CLASS_TABLE_LIMIT as usize];
        for code in 0x80u32..CLASS_TABLE_LIMIT {
            if classes[code as usize] & CLASS_INVARIANT != 0 {
                continue;
            }
            let Some(c) = char::from_u32(code) else {
                continue;
            };
            let starter = canonical_combining_class(c) == 0;
            let quick_yes = is_nfc_quick(std::iter::once(c)) == IsNormalized::Yes;
            let own_key = key_reference(c.encode_utf8(&mut [0; 4]));
            let mut affects = own_key.bytes().any(|b| b.is_ascii());
            if !(affects || starter && quick_yes) {
                // 앞의 ASCII 글자와 합쳐지거나 재배열되어 ASCII 글자를 바꾸는지 확인합니다.
                let mut pair = String::with_capacity(8);
                for ascii in 0x20u8..0x7F {
                    pair.clear();
                    pair.push(ascii as char);
                    pair.push(c);
                    let expected_prefix = (ascii as char).to_ascii_lowercase();
                    let pair_key = key_reference(&pair);
                    let mut expected = String::with_capacity(8);
                    expected.push(expected_prefix);
                    expected.push_str(&own_key);
                    if pair_key != expected {
                        affects = true;
                        break;
                    }
                }
            }
            table[code as usize] = affects;
        }
        table
    })
}

#[inline]
fn class_of(c: char) -> u8 {
    class_code(c as u32)
}

/// 변환해도 바뀌지 않고 앞 글자와 합쳐지지 않는 비ASCII 글자인지 알려 줍니다.
#[inline]
pub fn is_invariant(c: char) -> bool {
    class_of(c) & CLASS_INVARIANT != 0
}

/// ASCII 검색어의 일치 여부를 바꿀 수 있는 비ASCII 글자인지 알려 줍니다.
#[inline]
pub fn affects_ascii(c: char) -> bool {
    let code = c as u32;
    code < CLASS_TABLE_LIMIT && affects_table()[code as usize]
}

/// 불변이 아닌 비ASCII 글자마다 (글자, 관련 글자 목록, key의 ASCII 바이트)입니다.
///
/// 관련 글자는 `NFD(c)`와 `NFD(key(c))`에 들어 있는 글자입니다. 본문 글자가 검색어 일치에
/// 끼어들려면(대소문자 접기로 검색어 글자가 되거나, 정규화로 검색어 글자에 합쳐지거나)
/// 이 목록이 검색어의 분해 글자와 겹쳐야 합니다.
/// (글자, 관련 글자 목록, key의 ASCII 바이트)
type Profile = (char, Box<[char]>, Box<[u8]>);

fn noninvariant_profiles() -> &'static [Profile] {
    static PROFILES: OnceLock<Vec<Profile>> = OnceLock::new();
    PROFILES.get_or_init(|| {
        let table = classes();
        let mut profiles = Vec::new();
        for code in 0x80u32..CLASS_TABLE_LIMIT {
            if table[code as usize] & CLASS_INVARIANT != 0 {
                continue;
            }
            let Some(c) = char::from_u32(code) else {
                continue;
            };
            let own = c.encode_utf8(&mut [0; 4]).to_string();
            let folded = key_reference(&own);
            let mut related: Vec<char> = own.nfd().chain(folded.nfd()).collect();
            related.sort_unstable();
            related.dedup();
            let ascii: Vec<u8> = folded.bytes().filter(u8::is_ascii).collect();
            profiles.push((c, related.into_boxed_slice(), ascii.into_boxed_slice()));
        }
        profiles
    })
}

/// 원본 `position` 위치의 글자가 변환 대상(불변이 아닌 비ASCII 글자 또는 잘못된 UTF-8)인지 알려 줍니다.
pub fn variant_at(buf: &[u8], position: usize) -> bool {
    let Some(&lead) = buf.get(position) else {
        return false;
    };
    if lead < 0x80 {
        return false;
    }
    match decode_utf8(&buf[position..]) {
        Some((code, _)) => class_code(code) & CLASS_INVARIANT == 0,
        None => true,
    }
}

/// 문자열이 ASCII와 불변 글자로만 되어 있어 `key`가 ASCII 소문자화와 같은지 알려 줍니다.
pub fn is_key_trivial(text: &str) -> bool {
    text.is_ascii() || text.chars().all(|c| c.is_ascii() || is_invariant(c))
}

/// 비교 함수 `key`입니다. 결과는 `key_reference`와 항상 같습니다.
pub fn key(text: &str) -> String {
    if text.is_ascii() {
        return text.to_ascii_lowercase();
    }
    let mut output = String::with_capacity(text.len());
    key_into(text, &mut output);
    output
}

const HANGUL_L: std::ops::RangeInclusive<u32> = 0x1100..=0x1112;
const HANGUL_V: std::ops::RangeInclusive<u32> = 0x1161..=0x1175;
const HANGUL_T: std::ops::RangeInclusive<u32> = 0x11A8..=0x11C2;

/// `key(text)`를 `output`에 씁니다(기존 내용은 지움). 결과는 `key_reference`와 항상 같습니다.
///
/// 글자를 앞에서부터 보며 ASCII·불변 글자는 그대로(ASCII는 소문자로), 접기 결과가 불변 글자
/// 하나인 글자는 그 결과로 바꿉니다. 이 세 종류는 모두 결합 등급 0이고 NFC_QC=Yes라 앞 글자와
/// 합쳐지지 않으므로, 그 앞에서 문자열을 나눠 따로 변환해도 결과가 같습니다. 그 밖의 글자가
/// 나오면 바로 앞 글자(합쳐질 수 있는 유일한 글자)부터 다음 나눌 수 있는 글자 직전까지만
/// 정의대로 계산합니다. 분해형 한글 음절(첫소리+가운뎃소리[+끝소리])은 산술로 합칩니다.
pub fn key_into(text: &str, output: &mut String) {
    let mut out = std::mem::take(output).into_bytes();
    out.clear();
    out.reserve(text.len() + text.len() / 4);
    let two_byte = two_byte_table();
    let bytes = text.as_bytes();
    let len = bytes.len();
    // 바로 앞 글자의 입력 시작 위치와, 그 글자를 쓰기 전의 출력 길이입니다.
    let mut previous: Option<(usize, usize)> = None;
    let mut index = 0usize;
    while index < len {
        let byte = bytes[index];
        if byte < 0x80 {
            previous = Some((index, out.len()));
            out.push(byte.to_ascii_lowercase());
            index += 1;
            continue;
        }
        if (0xC2..=0xDF).contains(&byte) {
            // 2바이트 글자(라틴 악센트·그리스·키릴 등)는 표에서 바로 결과 바이트를 씁니다.
            let code = ((byte as usize & 0x1F) << 6) | (bytes[index + 1] as usize & 0x3F);
            let entry = two_byte[code];
            if entry != 0 {
                previous = Some((index, out.len()));
                let encoded = entry.to_le_bytes();
                out.extend_from_slice(&encoded[1..1 + (encoded[0] as usize)]);
                index += 2;
                continue;
            }
        } else if (0xEA..=0xED).contains(&byte) {
            // 한글 음절(U+AC00~U+D7A3)은 불변이므로 그대로 복사합니다.
            let code = ((byte as u32 & 0x0F) << 12)
                | ((bytes[index + 1] as u32 & 0x3F) << 6)
                | (bytes[index + 2] as u32 & 0x3F);
            if (0xAC00..=0xD7A3).contains(&code) {
                previous = Some((index, out.len()));
                out.extend_from_slice(&bytes[index..index + 3]);
                index += 3;
                continue;
            }
        }
        let c = text[index..].chars().next().unwrap();
        let width = c.len_utf8();
        let class = class_code(c as u32);
        if class & CLASS_INVARIANT != 0 {
            let code = c as u32;
            if HANGUL_L.contains(&code) {
                if let Some((syllable, used)) = compose_hangul(&text[index..]) {
                    previous = Some((index, out.len()));
                    push_char(&mut out, syllable);
                    index += used;
                    continue;
                }
            }
            previous = Some((index, out.len()));
            out.extend_from_slice(&bytes[index..index + width]);
            index += width;
            continue;
        }
        if class & CLASS_SIMPLE != 0 {
            previous = Some((index, out.len()));
            out.extend_from_slice(fold_char(c).unwrap().as_bytes());
            index += width;
            continue;
        }
        let (region_start, output_start) = previous.unwrap_or((index, out.len()));
        let mut end = index + width;
        while end < len {
            let next_byte = bytes[end];
            if next_byte < 0x80 {
                break;
            }
            let next = text[end..].chars().next().unwrap();
            if class_code(next as u32) & (CLASS_INVARIANT | CLASS_SIMPLE) != 0 {
                break;
            }
            end += next.len_utf8();
        }
        out.truncate(output_start);
        out.extend_from_slice(key_reference(&text[region_start..end]).as_bytes());
        // 다음 글자는 나눌 수 있는 글자이므로, 그 앞 구간은 다시 계산할 필요가 없습니다.
        previous = None;
        index = end;
    }
    // 위에서 쓴 바이트는 모두 완전한 UTF-8 글자입니다. 검증은 안전장치입니다.
    *output = String::from_utf8(out).expect("key output is valid UTF-8");
}

#[inline]
fn push_char(out: &mut Vec<u8>, c: char) {
    out.extend_from_slice(c.encode_utf8(&mut [0; 4]).as_bytes());
}

/// 2바이트 UTF-8 글자(U+0080~U+07FF)의 `key` 결과 바이트입니다(그대로이거나 접기 결과).
/// 첫 바이트는 결과 길이(1~2), 그 뒤가 결과 바이트입니다. 0이면 일반 경로로 처리합니다.
fn two_byte_table() -> &'static [u32] {
    static TABLE: OnceLock<Vec<u32>> = OnceLock::new();
    TABLE.get_or_init(|| {
        let classes = classes();
        let mut table = vec![0u32; 0x800];
        for code in 0x80u32..0x800 {
            let Some(c) = char::from_u32(code) else {
                continue;
            };
            let class = classes[code as usize];
            let mut own = [0u8; 4];
            let encoded: &[u8] = if class & CLASS_INVARIANT != 0 {
                c.encode_utf8(&mut own).as_bytes()
            } else if class & CLASS_SIMPLE != 0 {
                fold_char(c).unwrap().as_bytes()
            } else {
                continue;
            };
            if encoded.len() <= 2 {
                let mut entry = [0u8; 4];
                entry[0] = encoded.len() as u8;
                entry[1..1 + encoded.len()].copy_from_slice(encoded);
                table[code as usize] = u32::from_le_bytes(entry);
            }
        }
        table
    })
}

/// `key`에서 앞 글자와 나눠 변환해도 되는 글자(ASCII·불변·단순 접기)인지 알려 줍니다.
#[inline]
fn is_segment_start(c: char) -> bool {
    c.is_ascii() || class_code(c as u32) & (CLASS_INVARIANT | CLASS_SIMPLE) != 0
}

fn segment_start_at_or_before(text: &str, mut byte: usize) -> usize {
    while byte > 0 {
        let c = text[byte..].chars().next();
        if c.is_some_and(is_segment_start) {
            return byte;
        }
        byte -= text[..byte].chars().next_back().map_or(1, char::len_utf8);
    }
    0
}

fn segment_start_at_or_after(text: &str, mut byte: usize) -> usize {
    while byte < text.len() {
        let c = text[byte..].chars().next().unwrap();
        if is_segment_start(c) {
            return byte;
        }
        byte += c.len_utf8();
    }
    text.len()
}

/// `segment`(나눌 수 있는 글자에서 시작)의 `key` 결과에서 `converted` 바이트 위치가 원본의
/// 어느 바이트(그 위치를 만든 나눔 단위의 시작)에서 왔는지 돌려줍니다.
fn original_byte_of(segment: &str, converted: usize) -> usize {
    let mut produced = 0usize;
    let mut start = 0usize;
    while start < segment.len() {
        let first = segment[start..].chars().next().unwrap();
        let mut end = start + first.len_utf8();
        end = segment_start_at_or_after(segment, end);
        produced += key(&segment[start..end]).len();
        if produced > converted {
            return start;
        }
        start = end;
    }
    segment.len()
}

/// 미리보기용: `text`에서 `start_char`번째 글자부터 `key(검색어)`와 일치하는 첫 위치(글자 번호)입니다.
///
/// 검색과 같은 비교 기준입니다. 긴 줄도 메모리를 일정하게 쓰도록 64KiB 단위(나눌 수 있는 글자에서
/// 자름, 검색어 길이만큼 겹침)로 변환해 찾습니다. 위치는 그 일치를 만든 원본 글자 묶음의 시작입니다.
pub fn find_key_match_char(text: &str, pattern: &str, start_char: usize) -> Option<usize> {
    let pattern_key = key(pattern);
    if pattern_key.is_empty() {
        return None;
    }
    let finder = memchr::memmem::Finder::new(pattern_key.as_bytes());
    let begin = text
        .char_indices()
        .nth(start_char)
        .map_or(text.len(), |(byte, _)| byte);
    let overlap_chars = pattern.chars().count() * 3 + 8;
    let mut chunk_start = segment_start_at_or_before(text, begin);
    while chunk_start < text.len() {
        let mut chunk_end = (chunk_start + 65_536).min(text.len());
        while !text.is_char_boundary(chunk_end) {
            chunk_end += 1;
        }
        chunk_end = segment_start_at_or_after(text, chunk_end);
        let mut window_end = chunk_end;
        for c in text[chunk_end..].chars().take(overlap_chars) {
            window_end += c.len_utf8();
        }
        window_end = segment_start_at_or_after(text, window_end);
        let window = &text[chunk_start..window_end];
        let converted = key(window);
        let mut search_from = 0usize;
        while let Some(found) = finder.find(&converted.as_bytes()[search_from..]) {
            let byte = chunk_start + original_byte_of(window, search_from + found);
            if byte >= begin {
                return Some(text[..byte].chars().count());
            }
            search_from += found + 1;
        }
        if chunk_end >= text.len() {
            break;
        }
        chunk_start = chunk_end;
    }
    None
}

/// `text`가 첫소리+가운뎃소리[+끝소리] 자모로 시작하고 그 뒤 글자가 나눌 수 있는 글자이면
/// (합친 음절, 사용한 바이트 수)를 돌려줍니다. 뒤에 다른 결합 글자가 오면 None(정의대로 계산).
fn compose_hangul(text: &str) -> Option<(char, usize)> {
    let mut chars = text.chars();
    let lead = chars.next()? as u32;
    let vowel = chars.next()? as u32;
    if !HANGUL_V.contains(&vowel) {
        return None;
    }
    let mut code = 0xAC00 + ((lead - 0x1100) * 21 + (vowel - 0x1161)) * 28;
    let mut used = 6;
    let mut rest = chars.clone();
    if let Some(tail) = rest.next() {
        if HANGUL_T.contains(&(tail as u32)) {
            code += tail as u32 - 0x11A7;
            used += 3;
            chars = rest;
        }
    }
    match chars.next() {
        None => {}
        Some(next) if next.is_ascii() => {}
        Some(next) if class_code(next as u32) & (CLASS_INVARIANT | CLASS_SIMPLE) != 0 => {}
        Some(_) => return None,
    }
    char::from_u32(code).map(|syllable| (syllable, used))
}

/// 원본 바이트에서 다음 "변환이 필요한 글자"의 시작 위치를 찾습니다.
///
/// `ascii_pattern`이 참이면 ASCII 검색어 결과를 바꿀 수 있는 글자만, 거짓이면 불변이 아닌
/// 비ASCII 글자를 모두 찾습니다. 잘못된 UTF-8 바이트는 변환 대상으로 봅니다.
pub fn next_flagged(buf: &[u8], from: usize, ascii_pattern: bool) -> Option<usize> {
    let mut index = from;
    let len = buf.len();
    while index < len {
        // ASCII 구간은 8바이트씩 건너뜁니다.
        while index + 8 <= len {
            let word = u64::from_le_bytes(buf[index..index + 8].try_into().unwrap());
            if word & 0x8080_8080_8080_8080 != 0 {
                break;
            }
            index += 8;
        }
        if index >= len {
            return None;
        }
        let lead = buf[index];
        if lead < 0x80 {
            index += 1;
            continue;
        }
        let (code, width) = match decode_utf8(&buf[index..]) {
            Some(decoded) => decoded,
            None => {
                if ascii_pattern {
                    index += 1;
                    continue;
                }
                return Some(index);
            }
        };
        let flagged = if ascii_pattern {
            code < CLASS_TABLE_LIMIT && affects_table()[code as usize]
        } else {
            class_code(code) & CLASS_INVARIANT == 0
        };
        if flagged {
            return Some(index);
        }
        index += width;
    }
    None
}

#[inline]
fn decode_utf8(bytes: &[u8]) -> Option<(u32, usize)> {
    let lead = bytes[0];
    let (width, initial) = match lead {
        0xC2..=0xDF => (2, (lead & 0x1F) as u32),
        0xE0..=0xEF => (3, (lead & 0x0F) as u32),
        0xF0..=0xF4 => (4, (lead & 0x07) as u32),
        _ => return None,
    };
    if bytes.len() < width {
        return None;
    }
    let mut code = initial;
    for &byte in &bytes[1..width] {
        if byte & 0xC0 != 0x80 {
            return None;
        }
        code = (code << 6) | (byte & 0x3F) as u32;
    }
    let valid = match width {
        3 => code >= 0x800 && !(0xD800..=0xDFFF).contains(&code),
        4 => (0x1_0000..=0x10_FFFF).contains(&code),
        _ => true,
    };
    valid.then_some((code, width))
}

/// 검색어 하나에 대한 비교기입니다. 모든 형식이 이 비교기로 부분/정확 일치를 판정합니다.
pub struct Matcher {
    /// 문서 검증 전용: 어떤 값과도 일치하지 않습니다.
    never: bool,
    pat_key: String,
    pat_ascii: bool,
    /// `key(검색어)`를 ASCII 대소문자 무시로 찾는 자동자입니다(원본에서 쓸 수 있는 경우 전용).
    raw: AhoCorasick,
    /// `key(검색어)`에 ASCII 영문자가 없으면 대소문자 무시가 아무 영향이 없으므로, 원본 일치도
    /// 바이트 그대로 찾습니다(`folded`와 같은 탐색기, 짧은 값에서 훨씬 빠름).
    raw_exact: bool,
    /// 변환된 문자열에서 `key(검색어)`를 찾습니다.
    folded: memchr::memmem::Finder<'static>,
    /// 일반 텍스트용 유발 글자: 이 검색어의 일치에 끼어들 수 있는 글자입니다.
    /// 검색어가 ASCII와 불변 글자로만 되어 있을 때만 만듭니다.
    triggers: Option<Triggers>,
}

/// 원본 일치 탐색 결과(시작, 길이)를 차례로 돌려줍니다.
pub enum RawHits<'a> {
    Exact(Box<memchr::memmem::FindIter<'a, 'a>>, usize),
    Automaton(aho_corasick::FindIter<'a, 'a>),
}

impl Iterator for RawHits<'_> {
    type Item = (usize, usize);

    #[inline]
    fn next(&mut self) -> Option<(usize, usize)> {
        match self {
            RawHits::Exact(iter, length) => iter.next().map(|start| (start, *length)),
            RawHits::Automaton(iter) => iter.next().map(|hit| (hit.start(), hit.len())),
        }
    }
}

/// 유발 글자 목록과 그 UTF-8 첫 바이트입니다. 첫 바이트를 SIMD(memchr)로 찾은 뒤 글자를 확인합니다.
pub struct Triggers {
    chars: Vec<char>,
    leads: Vec<u8>,
    lead_table: [bool; 256],
}

impl Triggers {
    fn new(mut chars: Vec<char>) -> Self {
        chars.sort_unstable();
        chars.dedup();
        let mut leads: Vec<u8> = chars
            .iter()
            .map(|c| c.encode_utf8(&mut [0; 4]).as_bytes()[0])
            .collect();
        leads.sort_unstable();
        leads.dedup();
        let mut lead_table = [false; 256];
        for &lead in &leads {
            lead_table[lead as usize] = true;
        }
        Self {
            chars,
            leads,
            lead_table,
        }
    }

    /// `bytes` 안에 유발 글자가 하나라도 있는지 알려 줍니다.
    pub fn any_in(&self, bytes: &[u8]) -> bool {
        if bytes.is_ascii() {
            // 유발 글자는 모두 비ASCII입니다.
            return false;
        }
        if bytes.len() <= 64 {
            // 짧은 값은 SIMD 탐색을 시작하는 비용보다 바이트를 직접 보는 편이 빠릅니다.
            return bytes.iter().enumerate().any(|(index, &byte)| {
                self.lead_table[byte as usize]
                    && decode_utf8(&bytes[index..])
                        .and_then(|(code, _)| char::from_u32(code))
                        .is_some_and(|c| self.chars.binary_search(&c).is_ok())
            });
        }
        let mut found = false;
        self.scan(bytes, 0, bytes.len(), |_| {
            found = true;
            bytes.len()
        });
        found
    }

    /// `[start, end)` 안에서 유발 글자가 있는 위치를 `found`에 넘깁니다. `found`는 다음에 찾기
    /// 시작할 위치(보통 그 줄의 끝)를 돌려주므로, 한 줄에 유발 글자가 많아도 줄마다 한 번만 넘깁니다.
    /// 첫 바이트 묶음(세 개씩)마다 따로 훑으므로 위치 순서와 중복은 호출자가 정리합니다.
    pub fn scan(
        &self,
        buf: &[u8],
        start: usize,
        end: usize,
        mut found: impl FnMut(usize) -> usize,
    ) {
        if self.chars.is_empty() {
            return;
        }
        for group in self.leads.chunks(3) {
            let mut cursor = start;
            while cursor < end {
                let region = &buf[cursor..end];
                let next = match *group {
                    [a] => memchr::memchr(a, region),
                    [a, b] => memchr::memchr2(a, b, region),
                    [a, b, c] => memchr::memchr3(a, b, c, region),
                    _ => unreachable!(),
                };
                let Some(offset) = next else {
                    break;
                };
                let position = cursor + offset;
                // 글자는 구간 끝을 넘어서도 읽습니다(긴 줄을 창 단위로 나누면 글자가 창 경계에 걸칠 수 있음).
                let is_trigger = decode_utf8(&buf[position..])
                    .and_then(|(code, _)| char::from_u32(code))
                    .is_some_and(|c| self.chars.binary_search(&c).is_ok());
                cursor = if is_trigger {
                    found(position).max(position + 1)
                } else {
                    position + 1
                };
            }
        }
    }
}

impl Matcher {
    pub fn new(pattern: &str) -> Result<Self, String> {
        let pat_key = key(pattern);
        if pat_key.is_empty() || pat_key.contains(['\n', '\r']) {
            return Err(
                "Search text is empty or contains a line break after normalization.".into(),
            );
        }
        let raw = AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([pat_key.as_str()])
            .map_err(|error| error.to_string())?;
        let folded = memchr::memmem::Finder::new(pat_key.as_bytes()).into_owned();
        let triggers = if is_key_trivial(&pat_key) {
            Some(Triggers::new(trigger_chars(&pat_key)))
        } else {
            None
        };
        Ok(Self {
            never: false,
            pat_ascii: pat_key.is_ascii(),
            raw_exact: !pat_key.bytes().any(|byte| byte.is_ascii_alphabetic()),
            pat_key,
            raw,
            folded,
            triggers,
        })
    }

    /// 일반 텍스트용 유발 글자입니다. 없으면 줄마다 변환 필요 여부를 검사하는 경로를 씁니다.
    pub fn triggers(&self) -> Option<&Triggers> {
        self.triggers.as_ref()
    }

    /// 어떤 값과도 일치하지 않는 비교기입니다(문서 문법만 검증할 때 씁니다).
    pub fn never() -> Self {
        Self {
            never: true,
            pat_key: String::new(),
            pat_ascii: true,
            raw: AhoCorasick::new(std::iter::empty::<&str>()).expect("empty automaton"),
            raw_exact: false,
            folded: memchr::memmem::Finder::new(b"\xff").into_owned(),
            triggers: None,
        }
    }

    pub fn pat_key(&self) -> &str {
        &self.pat_key
    }

    pub fn pat_ascii(&self) -> bool {
        self.pat_ascii
    }

    /// 원본에서 `key(검색어)`를 ASCII 대소문자 무시로 찾은 (시작, 길이)들입니다(겹치지 않음, 앞에서부터).
    pub fn raw_hits_in<'a>(&'a self, haystack: &'a [u8]) -> RawHits<'a> {
        if self.raw_exact {
            RawHits::Exact(
                Box::new(self.folded.find_iter(haystack)),
                self.pat_key.len(),
            )
        } else {
            RawHits::Automaton(self.raw.find_iter(haystack))
        }
    }

    fn raw_find(&self, haystack: &[u8]) -> Option<(usize, usize)> {
        if self.never {
            return None;
        }
        if self.raw_exact {
            self.folded
                .find(haystack)
                .map(|start| (start, self.pat_key.len()))
        } else {
            self.raw.find(haystack).map(|hit| (hit.start(), hit.len()))
        }
    }

    /// 이 값(일치가 확인된 값)에서 원본 바이트로 찾은 일치 위치가 `key` 비교의 일치와 같은지
    /// 알려 줍니다. JSON·XML이 하이라이트 위치를 보고할지 정할 때 씁니다.
    pub fn raw_positions_valid(&self, value: &str) -> bool {
        if value.is_ascii() {
            return true;
        }
        if self.triggers.is_some() {
            return self.raw_hits_if_valid(value).is_some();
        }
        self.raw_search_valid(value)
    }

    /// 원본 그대로(ASCII 대소문자 무시) 찾아도 `key` 비교와 결과가 같은 문자열인지 알려 줍니다.
    pub fn raw_search_valid(&self, text: &str) -> bool {
        if text.is_ascii() {
            return true;
        }
        if self.pat_ascii {
            !text.chars().any(affects_ascii)
        } else {
            text.chars().all(|c| c.is_ascii() || is_invariant(c))
        }
    }

    /// 부분 일치: `key(검색어)`가 `key(값)` 안에 있으면 참입니다.
    pub fn contains(&self, value: &str) -> bool {
        if self.never {
            return false;
        }
        if let Some(triggers) = &self.triggers {
            // 검색어가 ASCII·불변 글자로만 된 경우입니다. 원본 일치 바로 뒤가 변환 대상 글자가 아니면
            // 그 일치는 `key(값)`에서도 그대로 일치입니다(일치의 첫 글자는 앞 글자와 합쳐지지 않고,
            // 바로 뒤 글자도 앞과 합쳐지지 않으므로 양쪽이 변환 경계). 원본 일치가 없을 때는 유발 글자가
            // 없어야 "일치 없음"으로 판정할 수 있습니다. ASCII 값도 같은 규칙으로 맞게 처리됩니다.
            let bytes = value.as_bytes();
            match self.raw_find(bytes) {
                Some((start, length)) if !variant_at(bytes, start + length) => return true,
                Some(_) => {}
                None if !triggers.any_in(bytes) => return false,
                None => {}
            }
            return self.folded.find(key(value).as_bytes()).is_some();
        }
        if value.is_ascii() {
            return self.pat_ascii && self.raw_find(value.as_bytes()).is_some();
        }
        match self.raw_hits_if_valid(value) {
            Some(hits) => !hits.is_empty(),
            None => self.folded.find(key(value).as_bytes()).is_some(),
        }
    }

    /// 원본에서 찾은 일치가 `key` 비교와 같은 결과임을 보장할 수 있으면 그 일치들을 돌려줍니다.
    ///
    /// 검색어가 ASCII·불변 글자로만 되어 있으면(유발 글자가 있는 경우) 일반 텍스트 경로와 같은
    /// 규칙을 씁니다: 값에 유발 글자가 없고 어떤 원본 일치 바로 뒤에도 변환 대상 글자가 없으면
    /// 원본 일치가 곧 결과입니다. 그 밖의 검색어는 값 전체가 ASCII·불변 글자일 때만 보장합니다.
    fn raw_hits_if_valid(&self, value: &str) -> Option<Vec<(usize, usize)>> {
        let bytes = value.as_bytes();
        if let Some(triggers) = &self.triggers {
            if triggers.any_in(bytes) {
                return None;
            }
            let mut hits = Vec::new();
            for (start, length) in self.raw_hits_in(bytes) {
                if variant_at(bytes, start + length) {
                    return None;
                }
                hits.push((start, length));
            }
            return Some(hits);
        }
        if self.raw_search_valid(value) {
            return Some(self.raw_hits_in(bytes).collect());
        }
        None
    }

    /// 부분 일치 개수와, 원본에서 찾은 경우 그 위치(바이트)입니다. 변환한 경우 위치는 없습니다.
    pub fn hits(&self, value: &str) -> Vec<Option<(usize, usize)>> {
        if self.never {
            return Vec::new();
        }
        if value.is_ascii() {
            if !self.pat_ascii {
                return Vec::new();
            }
            return self.raw_hits_in(value.as_bytes()).map(Some).collect();
        }
        if let Some(hits) = self.raw_hits_if_valid(value) {
            return hits.into_iter().map(Some).collect();
        }
        let converted = key(value);
        self.folded
            .find_iter(converted.as_bytes())
            .map(|_| None)
            .collect()
    }

    /// `text`를 `key`로 바꿔 `converted`에 쓰고, 그 안에서 `key(검색어)`가 시작하는 위치들을 돌려줍니다.
    pub fn find_in_converted(&self, text: &str, converted: &mut String) -> Vec<usize> {
        if self.never {
            return Vec::new();
        }
        key_into(text, converted);
        self.folded.find_iter(converted.as_bytes()).collect()
    }

    /// 정확히 일치: `key(값)`과 `key(검색어)`의 앞뒤 공백(Unicode White_Space)을 지운 결과가 같으면 참입니다.
    pub fn equals_trimmed(&self, value: &str) -> bool {
        if self.never {
            return false;
        }
        // 검색어 쪽 앞뒤 공백도 값과 같은 규칙으로 지웁니다(검수 F-1, 엔진을 직접 부르는 경우).
        // 공백뿐인 검색어는 어떤 값과도 일치하지 않습니다.
        let target = self.pat_key.trim();
        if target.is_empty() {
            return false;
        }
        if value.is_ascii() {
            return target.is_ascii() && value.trim().eq_ignore_ascii_case(target);
        }
        key(value).trim() == target
    }
}

/// 이 검색어의 일치에 끼어들 수 있는 "유발 글자"를 고릅니다(검색어가 ASCII와 불변 글자로만 된 경우).
///
/// - ASCII 검색어: `key(c)`에 검색어의 ASCII 글자가 들어 있는 글자(예: `K`(Kelvin)→k, `ß`→ss)만
///   일치를 새로 만들 수 있습니다.
/// - 그 밖의 검색어: 관련 글자가 검색어의 분해 글자와 겹치는 글자(예: 한글 중성·종성 자모)입니다.
///
/// 일치 바로 뒤에 붙은 변환 대상 글자(결합 부호 등)는 호출자가 `variant_at`으로 따로 확인합니다.
fn trigger_chars(pat_key: &str) -> Vec<char> {
    let mut chars = Vec::new();
    if pat_key.is_ascii() {
        let mut wanted = [false; 128];
        for byte in pat_key.bytes() {
            wanted[byte as usize] = true;
        }
        for (c, _, ascii) in noninvariant_profiles() {
            if ascii.iter().any(|&byte| wanted[byte as usize]) {
                chars.push(*c);
            }
        }
    } else {
        let mut wanted: Vec<char> = pat_key.nfd().chain(pat_key.chars()).collect();
        wanted.sort_unstable();
        wanted.dedup();
        for (c, related, _) in noninvariant_profiles() {
            if related.iter().any(|x| wanted.binary_search(x).is_ok()) {
                chars.push(*c);
            }
        }
    }
    chars
}

/// 줄 번호 계산기입니다. 줄바꿈은 LF, CRLF, CR이며 CRLF는 한 번으로 셉니다.
///
/// 줄을 하나씩 찾지 않고 `1 + LF 개수 + CR 개수 - CRLF 개수`로 계산합니다.
pub struct LineCounter {
    position: usize,
    line: usize,
    // `position`이 속한 줄의 시작 위치(`line_bounds`와 같은 정의)입니다.
    start: usize,
    // 줄바꿈이 없다고 이미 확인된 구간입니다(다시 세지 않음).
    break_free: Option<(usize, usize)>,
}

impl Default for LineCounter {
    fn default() -> Self {
        Self::new()
    }
}

impl LineCounter {
    pub fn new() -> Self {
        Self {
            position: 0,
            line: 1,
            start: 0,
            break_free: None,
        }
    }

    /// 지금까지 센 위치입니다.
    pub fn position(&self) -> usize {
        self.position
    }

    /// 호출자가 이미 센 결과(`position`이 `line`번째 줄, 그 줄이 `start`에서 시작)로 앞으로 옮깁니다.
    pub fn advance_known(&mut self, position: usize, line: usize, start: usize) {
        debug_assert!(position >= self.position);
        self.position = position;
        self.line = line;
        self.start = start;
        if let Some((_, to)) = self.break_free {
            if position >= to {
                self.break_free = None;
            }
        }
    }

    /// `[from, to)`에 CR·LF가 없음을 알려 줍니다. 이 구간을 지나는 줄 번호 계산에서 건너뜁니다.
    pub fn note_break_free(&mut self, from: usize, to: usize) {
        if from < to && self.position <= from {
            self.break_free = Some((from, to));
        }
    }

    /// `position` 위치가 속한 줄의 시작입니다(`line_bounds(buf, position).0`과 같음).
    /// 카운터가 이미 지나온 구간은 다시 훑지 않습니다.
    pub fn line_start(&self, buf: &[u8], position: usize) -> usize {
        if position < self.position {
            return memchr::memrchr2(b'\n', b'\r', &buf[..position]).map_or(0, |index| index + 1);
        }
        match memchr::memrchr2(b'\n', b'\r', &buf[self.position..position]) {
            Some(index) => self.position + index + 1,
            None => self.start,
        }
    }

    /// `target` 위치(앞으로만 이동)의 줄 번호를 돌려줍니다.
    pub fn line_at(&mut self, buf: &[u8], target: usize) -> usize {
        if let Some((from, to)) = self.break_free {
            if target > from && self.position <= from {
                self.break_free = None;
                self.line_at(buf, from);
                // [from, to)에는 줄바꿈이 없으므로 줄 번호와 줄 시작이 그대로입니다.
                self.position = target.min(to);
                if target < to {
                    self.break_free = Some((target, to));
                }
            } else if self.position >= to {
                self.break_free = None;
            }
        }
        if target > self.position {
            // CRLF 쌍: CR이 [position, target) 안에 있고 바로 뒤가 LF인 쌍입니다(LF가 target 뒤여도
            // 셉니다). CR마다 자기 구간에서 한 번만 세므로, CR 바로 뒤 위치의 줄 번호도 맞습니다.
            let (breaks, last_break) = if sparse_line_breaks(&buf[self.position..target]) {
                count_breaks_sparse(buf, self.position, target)
            } else {
                let (line_feeds, carriage_returns) = count_lf_cr(&buf[self.position..target]);
                let pair_end = (target + 1).min(buf.len());
                let breaks =
                    line_feeds + carriage_returns - count_crlf(&buf[self.position..pair_end]);
                let last = memchr::memrchr2(b'\n', b'\r', &buf[self.position..target])
                    .map(|index| self.position + index);
                (breaks, last)
            };
            self.line += breaks;
            if let Some(index) = last_break {
                self.start = index + 1;
            }
            self.position = target;
        }
        self.line
    }
}

/// 앞부분(최대 4KiB)의 평균 줄 길이가 256바이트보다 길면 참입니다. 두 방식의 결과는 같고,
/// 줄이 긴 파일은 줄바꿈 사이를 SIMD 탐색으로 건너뛰는 편이 빠릅니다(제안서 2.3.2).
fn sparse_line_breaks(region: &[u8]) -> bool {
    let sample = &region[..region.len().min(4096)];
    let breaks = sample
        .iter()
        .filter(|&&byte| byte == b'\n' || byte == b'\r')
        .count();
    breaks * 256 < sample.len()
}

/// `[start, end)`의 줄바꿈 수(LF + CR − CRLF, CR 뒤 LF는 `end` 뒤여도 봄)를 SIMD 탐색으로 셉니다.
/// 마지막 줄바꿈 바이트의 위치도 함께 돌려줍니다.
fn count_breaks_sparse(buf: &[u8], start: usize, end: usize) -> (usize, Option<usize>) {
    let mut breaks = 0usize;
    let mut last = None;
    for index in memchr::memchr2_iter(b'\n', b'\r', &buf[start..end]) {
        let position = start + index;
        last = Some(position);
        // CRLF는 LF에서 한 번 셉니다(CR 바로 뒤가 LF이면 CR은 세지 않음. LF가 구간 밖이어도 같음).
        if buf[position] == b'\r' && buf.get(position + 1) == Some(&b'\n') {
            continue;
        }
        breaks += 1;
    }
    (breaks, last)
}

/// 한 블록에서 바이트별 카운터가 넘치지 않는 최대 반복 수(255)만큼씩 32바이트 단위로 셉니다.
/// 고정 길이 배열에 비교 결과를 더하는 모양이라 컴파일러가 SIMD로 바꿉니다.
const COUNT_LANES: usize = 32;
const COUNT_BLOCK: usize = COUNT_LANES * 255;

fn count_lf_cr(data: &[u8]) -> (usize, usize) {
    let mut line_feeds = 0usize;
    let mut carriage_returns = 0usize;
    for block in data.chunks(COUNT_BLOCK) {
        let mut lf = [0u8; COUNT_LANES];
        let mut cr = [0u8; COUNT_LANES];
        let mut lanes = block.chunks_exact(COUNT_LANES);
        for lane in &mut lanes {
            for index in 0..COUNT_LANES {
                lf[index] += (lane[index] == b'\n') as u8;
                cr[index] += (lane[index] == b'\r') as u8;
            }
        }
        line_feeds += lf.iter().map(|&count| count as usize).sum::<usize>();
        carriage_returns += cr.iter().map(|&count| count as usize).sum::<usize>();
        for &byte in lanes.remainder() {
            line_feeds += (byte == b'\n') as usize;
            carriage_returns += (byte == b'\r') as usize;
        }
    }
    (line_feeds, carriage_returns)
}

/// `data` 안의 "CR 바로 뒤 LF" 쌍의 개수입니다.
fn count_crlf(data: &[u8]) -> usize {
    if data.len() < 2 {
        return 0;
    }
    let first = &data[..data.len() - 1];
    let second = &data[1..];
    let mut pairs = 0usize;
    for (first_block, second_block) in first.chunks(COUNT_BLOCK).zip(second.chunks(COUNT_BLOCK)) {
        let mut counts = [0u8; COUNT_LANES];
        let mut first_lanes = first_block.chunks_exact(COUNT_LANES);
        let mut second_lanes = second_block.chunks_exact(COUNT_LANES);
        for (a, b) in (&mut first_lanes).zip(&mut second_lanes) {
            for index in 0..COUNT_LANES {
                counts[index] += ((a[index] == b'\r') & (b[index] == b'\n')) as u8;
            }
        }
        pairs += counts.iter().map(|&count| count as usize).sum::<usize>();
        for (&a, &b) in first_lanes.remainder().iter().zip(second_lanes.remainder()) {
            pairs += ((a == b'\r') & (b == b'\n')) as usize;
        }
    }
    pairs
}

#[cfg(test)]
mod count_tests {
    #[test]
    fn block_counts_match_simple_counts() {
        let mut state = 0x5DEE_CE66_D1CE_4E5Bu64;
        for length in [0usize, 1, 2, 31, 32, 33, 8159, 8160, 8161, 20_000] {
            let data: Vec<u8> = (0..length)
                .map(|_| {
                    state ^= state << 13;
                    state ^= state >> 7;
                    state ^= state << 17;
                    [b'\r', b'\n', b'x'][(state % 3) as usize]
                })
                .collect();
            let lf = data.iter().filter(|&&b| b == b'\n').count();
            let cr = data.iter().filter(|&&b| b == b'\r').count();
            let pairs = data.windows(2).filter(|w| w == b"\r\n").count();
            assert_eq!(super::count_lf_cr(&data), (lf, cr));
            assert_eq!(super::count_crlf(&data), pairs);
            // 두 줄 세기 방식(블록·SIMD 탐색)이 여러 구간에서 같은 값을 내는지 확인합니다.
            for start in [0usize, 1, length / 3, length / 2] {
                for end in [
                    start,
                    start + 1,
                    length / 2 + 7,
                    length.saturating_sub(1),
                    length,
                ] {
                    if start > end || end > length {
                        continue;
                    }
                    let (lf, cr) = super::count_lf_cr(&data[start..end]);
                    let pair_end = (end + 1).min(length);
                    let dense = lf + cr - super::count_crlf(&data[start..pair_end]);
                    assert_eq!(
                        super::count_breaks_sparse(&data, start, end).0,
                        dense,
                        "{start}..{end}"
                    );
                }
            }
        }
    }
}

/// `position`이 들어 있는 줄의 시작과 끝(줄바꿈 제외)입니다.
pub fn line_bounds(buf: &[u8], position: usize) -> (usize, usize) {
    let start = memchr::memrchr2(b'\n', b'\r', &buf[..position]).map_or(0, |index| index + 1);
    let end =
        memchr::memchr2(b'\n', b'\r', &buf[position..]).map_or(buf.len(), |index| position + index);
    (start, end)
}

/// 문자열을 B안 줄바꿈(LF, CRLF, CR)으로 나눕니다. 줄 내용에는 줄바꿈이 없습니다.
/// 테스트의 기준 구현으로 씁니다.
#[cfg(test)]
pub fn split_lines(text: &str) -> impl Iterator<Item = &str> {
    LineSplit { rest: Some(text) }
}

#[cfg(test)]
struct LineSplit<'a> {
    rest: Option<&'a str>,
}

#[cfg(test)]
impl<'a> Iterator for LineSplit<'a> {
    type Item = &'a str;

    fn next(&mut self) -> Option<&'a str> {
        let rest = self.rest?;
        match memchr::memchr2(b'\n', b'\r', rest.as_bytes()) {
            Some(index) => {
                let terminator = if rest.as_bytes()[index] == b'\r'
                    && rest.as_bytes().get(index + 1) == Some(&b'\n')
                {
                    2
                } else {
                    1
                };
                let line = &rest[..index];
                let next = &rest[index + terminator..];
                self.rest = if next.is_empty() { None } else { Some(next) };
                Some(line)
            }
            None => {
                self.rest = None;
                Some(rest)
            }
        }
    }
}

#[cfg(test)]
mod key_into_tests {
    use super::*;

    /// 나누지 않고 한 번에 변환해 찾는 기준 구현입니다.
    fn unchunked_reference(text: &str, pattern: &str, start_char: usize) -> Option<usize> {
        let pat = key(pattern);
        let begin = text
            .char_indices()
            .nth(start_char)
            .map_or(text.len(), |(b, _)| b);
        let first = segment_start_at_or_before(text, begin);
        let converted = key(&text[first..]);
        let finder = memchr::memmem::Finder::new(pat.as_bytes());
        let mut from = 0usize;
        while let Some(found) = finder.find(&converted.as_bytes()[from..]) {
            let byte = first + original_byte_of(&text[first..], from + found);
            if byte >= begin {
                return Some(text[..byte].chars().count());
            }
            from += found + 1;
        }
        None
    }

    /// 미리보기 위치 찾기가 나누지 않고 찾은 기준과 같은지 무작위로 확인합니다.
    #[test]
    fn find_key_match_char_matches_brute_force() {
        let pool: Vec<char> = "ab \u{e9}\u{c9}\u{301}\u{df}\u{1100}\u{1161}\u{11a8}\u{ac00}\u{ac01}\u{3a3}\u{3c3}\u{41f}\u{43f}\u{212a}k"
            .chars()
            .collect();
        let patterns = [
            "\u{e9}",
            "ss",
            "\u{ac01}",
            "\u{3c3}",
            "k",
            "ab",
            "\u{43f}\u{43f}",
        ];
        let mut state = 0x1357_9BDF_2468_ACE0u64;
        for round in 0..20_000 {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            let length = (state % 40) as usize;
            let text: String = (0..length)
                .map(|step| pool[(state.rotate_left(step as u32 * 5) % pool.len() as u64) as usize])
                .collect();
            let pattern = patterns[round % patterns.len()];
            let start_char = (state % 5) as usize;
            assert_eq!(
                find_key_match_char(&text, pattern, start_char),
                unchunked_reference(&text, pattern, start_char),
                "{text:?} {pattern:?} {start_char}"
            );
        }
        // 64KiB 나눔 경계 근처에 일치를 넣은 긴 줄
        for offset in [65_530usize, 65_534, 65_536, 65_540, 131_070] {
            for pattern in ["\u{e9}", "ss", "\u{ac01}"] {
                let mut text = "x".repeat(offset);
                text.push_str("\u{c9}e\u{301}\u{df}\u{1100}\u{1161}\u{11a8}");
                text.push_str(&"y".repeat(70_000));
                for start_char in [0, offset - 3, offset + 2] {
                    assert_eq!(
                        find_key_match_char(&text, pattern, start_char),
                        unchunked_reference(&text, pattern, start_char),
                        "{offset} {pattern:?} {start_char}"
                    );
                }
            }
        }
        // 분해형 한글 긴 줄에서 완성형 검색어의 위치를 찾습니다(검수 R-3).
        let long: String = "\u{1100}\u{1161}".repeat(50_000) + "\u{1106}\u{1169}\u{11a8}";
        assert_eq!(find_key_match_char(&long, "\u{baa9}", 0), Some(100_000));
    }

    /// 결합 부호·자모·접기 대상이 섞인 무작위 문자열에서 `key_into`가 정의와 같은지 확인합니다.
    #[test]
    fn key_into_matches_reference_on_random_mixes() {
        let pool: Vec<char> = "aZ \u{e9}\u{c9}\u{df}\u{130}\u{131}\u{212A}\u{fb00}\u{1f0}\u{390}\u{3a3}\u{3c2}\u{38a}\u{41f}\u{439}\u{419}\u{1100}\u{1112}\u{1161}\u{1175}\u{11a8}\u{11c2}\u{ac00}\u{ac01}\u{d7a3}\u{300}\u{301}\u{308}\u{30c}\u{316}\u{327}\u{345}\u{1e9e}\u{1f80}\u{1fbc}\u{2126}\u{f900}\u{1d15e}\u{10400}\u{fffd}가목"
            .chars()
            .collect();
        let mut state = 0x2545_F491_4F6C_DD1Du64;
        let mut output = String::new();
        for _ in 0..200_000 {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            let length = (state % 9) as usize;
            let mut text = String::new();
            for step in 0..length {
                let pick = (state.rotate_left(step as u32 * 7) % pool.len() as u64) as usize;
                text.push(pool[pick]);
            }
            key_into(&text, &mut output);
            assert_eq!(output, key_reference(&text), "{:?}", text);
        }
    }

    /// 모든 스칼라값을 앞뒤 문맥(결합 부호, 자모, 접기 대상)과 붙여 확인합니다.
    #[test]
    fn key_into_matches_reference_for_every_scalar_in_context() {
        let mut output = String::new();
        let mut text = String::new();
        let contexts = [
            ("A", "\u{301}"),
            ("\u{1100}", "\u{11A8}"),
            ("\u{3a3}", "b"),
            ("", "\u{1161}"),
        ];
        for code in 0x80u32..0x11_0000 {
            let Some(c) = char::from_u32(code) else {
                continue;
            };
            for (before, after) in contexts {
                text.clear();
                text.push_str(before);
                text.push(c);
                text.push_str(after);
                key_into(&text, &mut output);
                assert_eq!(output, key_reference(&text), "U+{code:04X} in {:?}", text);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unicode_data_versions_match() {
        assert_eq!(UNICODE_VERSION, CASEFOLD_UNICODE_VERSION);
    }

    #[test]
    fn casefold_table_matches_the_ucd_file_exactly() {
        let source = include_str!("../data/CaseFolding-17.0.0.txt");
        assert!(source.starts_with("# CaseFolding-17.0.0.txt"));
        let mut expected = Vec::new();
        for line in source.lines() {
            let line = line.split('#').next().unwrap().trim();
            if line.is_empty() {
                continue;
            }
            let fields: Vec<&str> = line.split(';').map(str::trim).collect();
            if fields[1] != "C" && fields[1] != "F" {
                continue;
            }
            let code = u32::from_str_radix(fields[0], 16).unwrap();
            let mapped: String = fields[2]
                .split_whitespace()
                .map(|cp| char::from_u32(u32::from_str_radix(cp, 16).unwrap()).unwrap())
                .collect();
            expected.push((code, mapped));
        }
        expected.sort();
        assert_eq!(expected.len(), CASEFOLD.len());
        for ((code, mapped), (table_code, table_mapped)) in expected.iter().zip(CASEFOLD) {
            assert_eq!(code, table_code);
            assert_eq!(mapped, table_mapped);
        }
    }

    #[test]
    fn scalars_beyond_the_class_table_are_invariant() {
        for code in CLASS_TABLE_LIMIT..0x11_0000 {
            let Some(c) = char::from_u32(code) else {
                continue;
            };
            assert!(invariant_by_definition(c), "U+{code:04X}");
        }
    }

    #[test]
    fn key_is_idempotent_nfc_and_fast_paths_match_reference_for_every_scalar() {
        let mut buf = [0u8; 4];
        for code in 0u32..0x11_0000 {
            let Some(c) = char::from_u32(code) else {
                continue;
            };
            let text = c.encode_utf8(&mut buf);
            let reference = key_reference(text);
            assert_eq!(key(text), reference, "U+{code:04X}");
            assert_eq!(
                key_reference(&reference),
                reference,
                "idempotent U+{code:04X}"
            );
            assert!(
                unicode_normalization::is_nfc(&reference),
                "key result must be NFC for U+{code:04X}"
            );
            assert!(!reference.contains(['\n', '\r']) || text == "\n" || text == "\r");
        }
    }

    #[test]
    fn hangul_syllables_are_invariant() {
        for code in 0xAC00u32..=0xD7A3 {
            assert!(is_invariant(char::from_u32(code).unwrap()));
        }
    }

    #[test]
    fn known_folds_and_renormalization() {
        assert_eq!(key("Привет"), key("привет"));
        assert_eq!(key("Straße"), "strasse");
        assert_eq!(key("\u{212A}"), "k");
        assert_eq!(key("e\u{301}"), "\u{e9}");
        assert_eq!(key("\u{1100}\u{1161}\u{11A8}"), "\u{AC01}");
        // 접기 뒤 재정규화: ǰ는 j와 같지 않고, J̌와 같습니다.
        let matcher = Matcher::new("j").unwrap();
        assert!(!matcher.contains("\u{1F0}"));
        let matcher = Matcher::new("\u{3B9}").unwrap();
        assert!(!matcher.contains("\u{390}"));
        assert_eq!(key("\u{1F0}"), key("J\u{30C}"));
        assert_eq!(key("\u{1100}\u{1161}\u{11A8}\u{11A8}"), "\u{AC01}\u{11A8}");
        assert_eq!(
            key("\u{1100}\u{1161}\u{301}"),
            key_reference("\u{1100}\u{1161}\u{301}")
        );
    }

    #[test]
    fn affects_ascii_set_covers_folds_to_ascii_and_ascii_combining_marks() {
        for c in [
            '\u{DF}', '\u{130}', '\u{149}', '\u{17F}', '\u{212A}', '\u{FB00}', '\u{FB06}',
            '\u{1E9E}',
        ] {
            assert!(affects_ascii(c), "{c:?}");
        }
        for c in [
            '\u{300}', '\u{301}', '\u{308}', '\u{327}', '\u{338}', '\u{344}',
        ] {
            assert!(affects_ascii(c), "{c:?}");
        }
        for c in ['\u{AC00}', '\u{416}', '\u{3A3}', '\u{4E00}'] {
            assert!(!affects_ascii(c), "{c:?}");
        }
    }

    fn reference_contains(pattern: &str, value: &str) -> bool {
        key_reference(value).contains(&key_reference(pattern))
    }

    #[test]
    fn matcher_agrees_with_reference_on_random_strings() {
        let alphabet: Vec<char> = "aAeEiIjJkKsSzZ \t-.\u{DF}\u{130}\u{131}\u{17F}\u{212A}\u{1F0}\u{390}\u{3B9}\u{399}\u{301}\u{308}\u{30C}\u{338}\u{344}\u{316}\u{345}\u{E9}\u{C9}\u{410}\u{430}\u{3A3}\u{3C3}\u{3C2}\u{AC00}\u{AC01}\u{1100}\u{1161}\u{11A8}\u{FB01}\u{1E9E}\u{2000}=<"
            .chars()
            .collect();
        let mut state = 0x2545_F491_4F6C_DD1Du64;
        let mut next = |bound: usize| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            (state % bound as u64) as usize
        };
        for _ in 0..40_000 {
            let pattern: String = (0..1 + next(3))
                .map(|_| alphabet[next(alphabet.len())])
                .collect();
            let value: String = (0..next(12))
                .map(|_| alphabet[next(alphabet.len())])
                .collect();
            let Ok(matcher) = Matcher::new(&pattern) else {
                continue;
            };
            assert_eq!(
                matcher.contains(&value),
                reference_contains(&pattern, &value),
                "pattern={pattern:?} value={value:?}"
            );
            assert_eq!(
                matcher.equals_trimmed(&value),
                !key_reference(&pattern).trim().is_empty()
                    && key_reference(&value).trim() == key_reference(&pattern).trim(),
                "exact pattern={pattern:?} value={value:?}"
            );
            for hit in matcher.hits(&value).into_iter().flatten() {
                let raw = &value[hit.0..hit.0 + hit.1];
                assert_eq!(
                    key_reference(raw),
                    key_reference(&pattern),
                    "position {value:?}"
                );
            }
        }
    }

    fn reference_lines(buf: &[u8]) -> Vec<usize> {
        // 위치마다 B안 줄 번호를 하나씩 계산합니다.
        let mut lines = Vec::with_capacity(buf.len() + 1);
        let mut line = 1;
        for index in 0..=buf.len() {
            lines.push(line);
            if index < buf.len() {
                let byte = buf[index];
                if byte == b'\n' || (byte == b'\r' && buf.get(index + 1) != Some(&b'\n')) {
                    line += 1;
                }
            }
        }
        lines
    }

    #[test]
    fn line_counter_matches_reference_on_random_line_breaks() {
        let mut state = 0x9E37_79B9_7F4A_7C15u64;
        for round in 0..600 {
            // 짝수 회차는 줄바꿈이 많은 입력, 홀수 회차는 줄이 긴 입력(SIMD 탐색 방식)입니다.
            let len = if round % 2 == 0 {
                (state % 300) as usize
            } else {
                (state % 6000) as usize
            };
            let mut buf = Vec::with_capacity(len);
            for _ in 0..len {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                let byte = if round % 2 == 0 || state.is_multiple_of(400) {
                    [b'a', b'\r', b'\n', b'x'][((state >> 9) % 4) as usize]
                } else {
                    b'a'
                };
                buf.push(byte);
            }
            let expected = reference_lines(&buf);
            let mut counter = LineCounter::new();
            let mut position = 0;
            let mut noted_up_to = 0usize;
            while position <= buf.len() {
                // 줄바꿈 없는 구간 알림을 섞어도 결과가 같은지 확인합니다.
                if noted_up_to <= position && position + 2 < buf.len() {
                    let from = position + 1;
                    let to =
                        memchr::memchr2(b'\n', b'\r', &buf[from..]).map_or(buf.len(), |i| from + i);
                    counter.note_break_free(from, to);
                    noted_up_to = to;
                }
                assert_eq!(
                    counter.line_start(&buf, position),
                    line_bounds(&buf, position).0,
                    "line start {buf:?} at {position}"
                );
                assert_eq!(
                    counter.line_at(&buf, position),
                    expected[position],
                    "{buf:?} at {position}"
                );
                assert_eq!(
                    counter.line_start(&buf, position),
                    line_bounds(&buf, position).0,
                    "line start after count {buf:?} at {position}"
                );
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                position += 1 + (state % 5) as usize;
            }
        }
    }

    #[test]
    fn split_lines_uses_lf_crlf_and_cr() {
        let lines: Vec<&str> = split_lines("a\r\nb\rc\nd\r\r\ne\n\r").collect();
        assert_eq!(lines, ["a", "b", "c", "d", "", "e", ""]);
    }

    #[test]
    fn next_flagged_skips_ascii_and_hangul() {
        // 소문자 é(U+00E9)는 변환해도 같으므로 불변이고, 대문자 É는 변환 대상입니다.
        let text = "hello 한글 world é É end".as_bytes();
        let position = next_flagged(text, 0, false).unwrap();
        assert_eq!(&text[position..position + 2], "É".as_bytes());
        assert_eq!(next_flagged("abc 한글".as_bytes(), 0, false), None);
        assert_eq!(next_flagged("Привет abc".as_bytes(), 0, true), None);
        assert!(next_flagged("Привет abc".as_bytes(), 0, false).is_some());
    }
}
