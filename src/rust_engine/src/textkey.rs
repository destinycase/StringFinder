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
const CLASS_AFFECTS_ASCII: u8 = 2;

/// 모든 코드포인트의 분류표(첫 사용 때 한 번 계산)입니다.
fn classes() -> &'static [u8] {
    static TABLE: OnceLock<Vec<u8>> = OnceLock::new();
    TABLE.get_or_init(|| {
        let mut table = vec![0u8; 0x11_0000];
        for code in 0x80u32..0x11_0000 {
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
            table[code as usize] = if affects { CLASS_AFFECTS_ASCII } else { 0 };
        }
        table
    })
}

#[inline]
fn class_of(c: char) -> u8 {
    classes()[c as usize]
}

/// 변환해도 바뀌지 않고 앞 글자와 합쳐지지 않는 비ASCII 글자인지 알려 줍니다.
#[inline]
pub fn is_invariant(c: char) -> bool {
    class_of(c) & CLASS_INVARIANT != 0
}

/// ASCII 검색어의 일치 여부를 바꿀 수 있는 비ASCII 글자인지 알려 줍니다.
#[inline]
pub fn affects_ascii(c: char) -> bool {
    class_of(c) & CLASS_AFFECTS_ASCII != 0
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
        for code in 0x80u32..0x11_0000 {
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
        Some((code, _)) => classes()[code as usize] & CLASS_INVARIANT == 0,
        None => true,
    }
}

/// 문자열이 ASCII와 불변 글자로만 되어 있어 `key`가 ASCII 소문자화와 같은지 알려 줍니다.
pub fn is_key_trivial(text: &str) -> bool {
    text.is_ascii() || text.chars().all(|c| c.is_ascii() || is_invariant(c))
}

/// 비교 함수 `key`입니다. 결과는 `key_reference`와 항상 같습니다.
pub fn key(text: &str) -> String {
    if is_key_trivial(text) {
        return text.to_ascii_lowercase();
    }
    key_reference(text)
}

/// 원본 바이트에서 다음 "변환이 필요한 글자"의 시작 위치를 찾습니다.
///
/// `ascii_pattern`이 참이면 ASCII 검색어 결과를 바꿀 수 있는 글자만, 거짓이면 불변이 아닌
/// 비ASCII 글자를 모두 찾습니다. 잘못된 UTF-8 바이트는 변환 대상으로 봅니다.
pub fn next_flagged(buf: &[u8], from: usize, ascii_pattern: bool) -> Option<usize> {
    let table = classes();
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
        let class = table[code as usize];
        let flagged = if ascii_pattern {
            class & CLASS_AFFECTS_ASCII != 0
        } else {
            class & CLASS_INVARIANT == 0
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
    /// 변환된 문자열에서 `key(검색어)`를 찾습니다.
    folded: memchr::memmem::Finder<'static>,
    /// 일반 텍스트용 유발 글자: 이 검색어의 일치에 끼어들 수 있는 글자입니다.
    /// 검색어가 ASCII와 불변 글자로만 되어 있을 때만 만듭니다.
    triggers: Option<Triggers>,
}

/// 유발 글자 목록과 그 UTF-8 첫 바이트입니다. 첫 바이트를 SIMD(memchr)로 찾은 뒤 글자를 확인합니다.
pub struct Triggers {
    chars: Vec<char>,
    leads: Vec<u8>,
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
        Self { chars, leads }
    }

    /// `[start, end)` 안에서 유발 글자가 있는 위치를 차례로 `found`에 넘깁니다.
    pub fn scan(&self, buf: &[u8], start: usize, end: usize, mut found: impl FnMut(usize)) {
        if self.chars.is_empty() {
            return;
        }
        let region = &buf[start..end];
        let mut check = |offset: usize| {
            if let Some((code, _)) = decode_utf8(&region[offset..]) {
                if let Some(c) = char::from_u32(code) {
                    if self.chars.binary_search(&c).is_ok() {
                        found(start + offset);
                    }
                }
            }
        };
        // 첫 바이트를 세 개씩 묶어 찾습니다. 같은 위치를 두 번 넘기지 않도록 묶음마다 따로 훑고
        // 결과는 위치 순서와 관계없이 호출자가 정렬합니다.
        for group in self.leads.chunks(3) {
            match *group {
                [a] => memchr::memchr_iter(a, region).for_each(&mut check),
                [a, b] => memchr::memchr2_iter(a, b, region).for_each(&mut check),
                [a, b, c] => memchr::memchr3_iter(a, b, c, region).for_each(&mut check),
                _ => unreachable!(),
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

    pub fn raw_finder(&self) -> &AhoCorasick {
        &self.raw
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
        if value.is_ascii() {
            return self.pat_ascii && self.raw.find(value).is_some();
        }
        if self.raw_search_valid(value) {
            return self.raw.find(value).is_some();
        }
        self.folded.find(key_reference(value).as_bytes()).is_some()
    }

    /// 부분 일치 개수와, 원본에서 찾은 경우 그 위치(바이트)입니다. 변환한 경우 위치는 없습니다.
    pub fn hits(&self, value: &str) -> Vec<Option<(usize, usize)>> {
        if self.never {
            return Vec::new();
        }
        if (value.is_ascii() && self.pat_ascii)
            || (!value.is_ascii() && self.raw_search_valid(value))
        {
            return self
                .raw
                .find_iter(value)
                .map(|hit| Some((hit.start(), hit.len())))
                .collect();
        }
        if value.is_ascii() {
            return Vec::new();
        }
        let converted = key_reference(value);
        self.folded
            .find_iter(converted.as_bytes())
            .map(|_| None)
            .collect()
    }

    /// 정확히 일치: `key(값)`의 앞뒤 공백(Unicode White_Space)을 지운 결과가 `key(검색어)`와 같으면 참입니다.
    pub fn equals_trimmed(&self, value: &str) -> bool {
        if self.never {
            return false;
        }
        if value.is_ascii() {
            return self.pat_ascii && value.trim().eq_ignore_ascii_case(&self.pat_key);
        }
        key(value).trim() == self.pat_key
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
        }
    }

    /// `target` 위치(앞으로만 이동)의 줄 번호를 돌려줍니다.
    pub fn line_at(&mut self, buf: &[u8], target: usize) -> usize {
        if target > self.position {
            let (line_feeds, carriage_returns) = count_lf_cr(&buf[self.position..target]);
            // CRLF 쌍: CR이 [position, target) 안에 있고 바로 뒤가 LF인 쌍입니다(LF가 target 뒤여도
            // 셉니다). CR마다 자기 구간에서 한 번만 세므로, CR 바로 뒤 위치의 줄 번호도 맞습니다.
            let pair_end = (target + 1).min(buf.len());
            let pairs = count_crlf(&buf[self.position..pair_end]);
            self.line += line_feeds + carriage_returns - pairs;
            self.position = target;
        }
        self.line
    }
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
                key_reference(&value).trim() == key_reference(&pattern),
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
        for _ in 0..300 {
            let len = (state % 300) as usize;
            let mut buf = Vec::with_capacity(len);
            for _ in 0..len {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                buf.push([b'a', b'\r', b'\n', b'x'][(state % 4) as usize]);
            }
            let expected = reference_lines(&buf);
            let mut counter = LineCounter::new();
            let mut position = 0;
            while position <= buf.len() {
                assert_eq!(
                    counter.line_at(&buf, position),
                    expected[position],
                    "{buf:?} at {position}"
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
