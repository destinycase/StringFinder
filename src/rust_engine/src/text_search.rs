//! 텍스트 검색 본체(UTF-8, CP949, UTF-16, 긴 줄, 줄 번호).
//! 8.1.5(엔진 1.0.1)에서 lib.rs에서 옮겼습니다. 함수 본문은 그대로입니다.

use super::*;

/// 일반 텍스트를 이 크기의 조각(줄 경계에 맞춤)으로 나눠, 조각이 CPU 캐시에 있는 동안
/// 변환 필요 여부 검사와 검색을 이어서 합니다.
pub(crate) const TEXT_CHUNK_SIZE: usize = 256 * 1024;

#[allow(clippy::too_many_arguments)]
pub(crate) fn do_search_with_mmap(
    mmap: &[u8],
    encoding: &'static Encoding,
    matcher: &textkey::Matcher,
    is_exact: bool,

    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
) -> Vec<RawMatch> {
    let mut results = Vec::new();
    let had_decode_errors = if encoding == UTF_8 {
        search_utf8_text(
            mmap,
            matcher,
            is_exact,
            stop_flag,
            max_per_file,
            &mut results,
        )
    } else {
        // Non-UTF8 일반 텍스트는 파일 전체를 String으로 복사하지 않고 청크 단위로 디코딩합니다.
        matches!(
            search_non_utf8_chunks(
                mmap,
                encoding,
                matcher,
                is_exact,
                stop_flag,
                max_per_file,
                &mut results,
                false,
            ),
            ChunkDecode::Completed { had_errors: true }
        )
    };
    if had_decode_errors {
        // 해석하지 못한 바이트가 있으면 일치가 없어도 파일 단위 안내를 남깁니다(제안서 2.5).
        results.push((
            0,
            format!("{MATCH_META_DECODE_PARTIAL_PREFIX}{}", encoding.name()),
            None,
            None,
            ResultKind::DecodePartial,
        ));
    }
    results
}

#[inline]
pub(crate) fn is_ascii_chunk(bytes: &[u8]) -> bool {
    // 4KiB 블록마다 모든 바이트를 OR로 모아 최상위 비트를 봅니다. 컴파일러가 SIMD로 바꾸므로
    // 표준 `is_ascii`(8바이트 단위)보다 빠르고, 비ASCII 블록을 만나면 바로 멈춥니다.
    bytes
        .chunks(4096)
        .all(|block| block.iter().fold(0u8, |acc, &byte| acc | byte) < 0x80)
}

/// UTF-8 일반 텍스트 검색 상태입니다.
///
/// 파일을 줄로 나누지 않습니다. 변환이 필요한 글자가 있는 줄만 `key()`로 바꿔 비교하고,
/// 나머지는 원본 그대로 `key(검색어)`를 ASCII 대소문자 무시로 찾습니다(결과가 같음이 증명된 경로).
pub(crate) struct Utf8TextSearch<'a> {
    buf: &'a [u8],
    matcher: &'a textkey::Matcher,
    is_exact: bool,
    max_per_file: usize,
    stop_flag: &'a Arc<AtomicBool>,
    counter: textkey::LineCounter,
    // 같은 줄의 여러 일치에서 줄 경계를 다시 찾지 않도록 마지막 줄과 그 줄 번호를 기억합니다.
    line: Option<(usize, usize)>,
    line_no: Option<usize>,
    last_exact_line_start: Option<usize>,
    // 한 줄에 일치가 여러 번 있을 때 같은 줄 내용을 다시 만들지 않도록 기억합니다(긴 줄 제외).
    last_raw_line: Option<(usize, String)>,
    results: &'a mut Vec<RawMatch>,
    // UTF-8 유효성을 확인한 위치와, 해석하지 못한 바이트를 만났는지 여부입니다.
    utf8_checked: usize,
    invalid_utf8: bool,
}

impl Utf8TextSearch<'_> {
    fn full(&self) -> bool {
        self.results.len() > self.max_per_file
    }

    /// `[start, end)`까지 UTF-8로 올바른지 확인합니다. 이미 확인한 위치부터 이어서 보며,
    /// ASCII로 확인된 구간은 다시 검사하지 않습니다. 구간 끝에서 잘린 글자는 다음 구간에서 봅니다.
    fn check_utf8(&mut self, start: usize, end: usize, ascii: bool) {
        if self.invalid_utf8 || end <= self.utf8_checked {
            return;
        }
        if ascii && self.utf8_checked >= start {
            self.utf8_checked = end;
            return;
        }
        match simdutf8::compat::from_utf8(&self.buf[self.utf8_checked..end]) {
            Ok(_) => self.utf8_checked = end,
            Err(error) if error.error_len().is_none() && end < self.buf.len() => {
                self.utf8_checked += error.valid_up_to();
            }
            Err(_) => self.invalid_utf8 = true,
        }
    }

    /// `position`의 줄 번호와 줄 경계입니다(앞으로만 이동).
    ///
    /// 일치가 촘촘한 파일에서는 일치마다 줄 번호 계산과 줄 경계 찾기를 따로 하면 비용이 큽니다.
    /// 그래서 기억한 줄의 바로 다음 몇 줄 안에 있으면, 줄 끝에서 다음 줄바꿈까지만 찾으며 줄 번호를
    /// 1씩 올립니다(줄바꿈 하나 = LF·CR·CRLF 하나라 카운터와 같은 값). 멀면 카운터로 셉니다.
    fn locate(&mut self, position: usize) -> (usize, (usize, usize)) {
        const NEAR_LINES: usize = 8;
        if let Some((start, end)) = self.line {
            if start <= position && position < end {
                let line = match self.line_no {
                    Some(line) => line,
                    None => self.counter.line_at(self.buf, position),
                };
                self.line_no = Some(line);
                return (line, (start, end));
            }
            if let Some(mut line) = self.line_no {
                if position > end && self.counter.position() <= end {
                    let mut line_end = end;
                    for _ in 0..NEAR_LINES {
                        let next_start = skip_line_break(self.buf, line_end);
                        if next_start == line_end {
                            break;
                        }
                        line += 1;
                        line_end = next_line_break(self.buf, next_start);
                        if position < next_start {
                            break;
                        }
                        if position < line_end {
                            self.counter.advance_known(position, line, next_start);
                            self.line = Some((next_start, line_end));
                            self.line_no = Some(line);
                            return (line, (next_start, line_end));
                        }
                    }
                }
            }
        }
        let line = self.counter.line_at(self.buf, position);
        let bounds = self.bounds(position);
        self.line_no = Some(line);
        (line, bounds)
    }

    fn bounds(&mut self, position: usize) -> (usize, usize) {
        if let Some((start, end)) = self.line {
            if start <= position && position < end {
                return (start, end);
            }
        }
        self.line_no = None;
        // 줄 시작은 줄 번호 카운터가 지나온 곳까지만 거꾸로 찾습니다(긴 줄에서 파일 처음까지 훑지 않음).
        let start = self.counter.line_start(self.buf, position);
        let end = memchr::memchr2(b'\n', b'\r', &self.buf[position..])
            .map_or(self.buf.len(), |index| position + index);
        let bounds = (start, end);
        self.line = Some(bounds);
        bounds
    }

    /// 원본 그대로 찾아도 되는 구간입니다. 중지되었거나 상한을 넘으면 참을 돌려줍니다.
    fn raw_region(&mut self, start: usize, end: usize) -> bool {
        if start >= end {
            return false;
        }
        let matcher = self.matcher;
        for (hit_start, hit_len) in matcher.raw_hits_in(&self.buf[start..end]) {
            if self.full() {
                return true;
            }
            if self.results.len().is_multiple_of(1000) && self.stop_flag.load(Ordering::Relaxed) {
                return true;
            }
            self.raw_hit(start + hit_start, hit_len);
        }
        false
    }

    /// 원본에서 찾은 일치 하나를 기록합니다(줄에 변환 대상이 없음이 확인된 경우).
    fn raw_hit(&mut self, match_start: usize, match_len: usize) {
        let (line, (line_start, line_end)) = self.locate(match_start);
        if self.is_exact {
            if self.last_exact_line_start == Some(line_start) {
                return;
            }
            self.last_exact_line_start = Some(line_start);
            let text = String::from_utf8_lossy(&self.buf[line_start..line_end]);
            if self.matcher.equals_trimmed(&text) {
                let (content, kind) =
                    extract_line_content_bytes(self.buf, line_start, line_end, None, None);
                self.results.push((line, content, None, None, kind));
            }
            return;
        }
        // 같은 줄의 다음 일치는 앞서 만든 줄 내용을 그대로 씁니다. 긴 줄은 일치 위치마다 미리보기가 달라 캐시하지 않습니다.
        if let Some((cached_start, content)) = &self.last_raw_line {
            if *cached_start == line_start {
                let content = content.clone();
                self.results.push((
                    line,
                    content,
                    Some(match_start),
                    Some(match_len),
                    ResultKind::Match,
                ));
                return;
            }
        }
        // Each hit retains its line preview and byte range.
        let (content, kind) = extract_line_content_bytes(
            self.buf,
            line_start,
            line_end,
            Some(match_start),
            Some(match_len),
        );
        // 긴 줄은 미리보기만 보이므로 위치를 생략합니다. 내용 글자가 아니라 만든 곳이 알려 준 종류로 판단합니다.
        let (offset, length) = if kind == ResultKind::LongLine {
            (None, None)
        } else {
            (Some(match_start), Some(match_len))
        };
        if kind == ResultKind::Match {
            self.last_raw_line = Some((line_start, content.clone()));
        }
        self.results.push((line, content, offset, length, kind));
    }

    /// 변환할 줄 목록(위치 순)을 처리합니다. 바로 이어진 줄들은 한 번에 변환해 검색합니다.
    fn converted_lines(&mut self, lines: &[(usize, usize)]) {
        let mut index = 0usize;
        while index < lines.len() {
            if self.full() {
                return;
            }
            let mut last = index;
            while last + 1 < lines.len()
                && lines[last + 1].0 == skip_line_break(self.buf, lines[last].1)
            {
                last += 1;
            }
            if self.is_exact || last == index {
                for &(line_start, line_end) in &lines[index..=last] {
                    self.converted_line(line_start, line_end);
                    if self.full() {
                        return;
                    }
                }
            } else {
                self.converted_run(&lines[index..=last]);
            }
            index = last + 1;
        }
    }

    /// 바로 이어진 줄들(부분 일치)을 한 번에 변환해 찾습니다. `key`는 CR·LF를 그대로 두고 새로
    /// 만들지도 않으므로, 변환 결과의 k번째 줄이 원본의 k번째 줄입니다.
    fn converted_run(&mut self, lines: &[(usize, usize)]) {
        let region_start = lines[0].0;
        let region_end = lines[lines.len() - 1].1;
        let text = String::from_utf8_lossy(&self.buf[region_start..region_end]);
        let mut converted = String::new();
        let hits = self.matcher.find_in_converted(&text, &mut converted);
        if hits.is_empty() {
            return;
        }
        let converted = converted.as_bytes();
        let mut line_index = 0usize;
        let mut converted_line_start = 0usize;
        let mut converted_line_end = next_line_break(converted, 0);
        let mut last_reported: Option<(usize, (String, ResultKind))> = None;
        for hit in hits {
            while hit >= converted_line_end {
                converted_line_start = skip_line_break(converted, converted_line_end);
                converted_line_end = next_line_break(converted, converted_line_start);
                line_index += 1;
            }
            debug_assert!(hit >= converted_line_start);
            let (line_start, line_end) = lines[line_index];
            let line = self.counter.line_at(self.buf, line_start);
            let content = match &last_reported {
                Some((reported, content)) if *reported == line_start => content.clone(),
                _ => extract_line_content_bytes(self.buf, line_start, line_end, None, None),
            };
            if self.full() {
                return;
            }
            self.results
                .push((line, content.0.clone(), None, None, content.1));
            last_reported = Some((line_start, content));
        }
    }

    /// 변환이 필요한 줄입니다. 원본 위치를 알 수 없으므로 위치는 생략합니다(틀린 위치 금지).
    fn converted_line(&mut self, line_start: usize, line_end: usize) {
        let text = String::from_utf8_lossy(&self.buf[line_start..line_end]);
        let line = self.counter.line_at(self.buf, line_start);
        if self.is_exact {
            if self.matcher.equals_trimmed(&text) {
                let (content, kind) =
                    extract_line_content_bytes(self.buf, line_start, line_end, None, None);
                self.results.push((line, content, None, None, kind));
            }
            return;
        }
        let hits = self.matcher.hits(&text).len();
        if hits == 0 {
            return;
        }
        let (content, kind) =
            extract_line_content_bytes(self.buf, line_start, line_end, None, None);
        for _ in 0..hits {
            if self.full() {
                return;
            }
            self.results.push((line, content.clone(), None, None, kind));
        }
    }
}

/// 한 조각보다 긴 줄을 창(조각 크기) 단위로 처리합니다. 창마다 줄바꿈 찾기·ASCII 검사·원본 검색을
/// 캐시에 있는 동안 이어서 하므로 긴 줄을 여러 번 훑지 않습니다. 줄 전체의 변환 여부는 일반 경로와
/// 같은 규칙(유발 글자가 있거나 원본 일치 바로 뒤가 변환 대상 글자이면 변환)으로 정하고, 원본 일치는
/// 줄이 끝날 때까지 모았다가 변환하지 않는 경우에만 기록합니다. 줄 끝 위치를 돌려주며, 상한에
/// 도달하거나 중지되면 None입니다.
pub(crate) fn search_long_line(
    search: &mut Utf8TextSearch<'_>,
    triggers: &textkey::Triggers,
    line_start: usize,
) -> Option<usize> {
    let buf = search.buf;
    let len = buf.len();
    let matcher = search.matcher;
    // 창 경계에 걸친 일치를 놓치지 않도록 검색어 길이만큼 겹쳐 찾습니다.
    let overlap = search.matcher.pat_key().len().saturating_sub(1);
    let mut pending: Vec<(usize, usize)> = Vec::new();
    let mut convert = false;
    let mut window_start = line_start;
    let mut search_from = line_start;
    // 보고할 것이 없어 줄 경계를 찾지 않고 건너뛴 구간의 시작입니다.
    let mut skipped_from: Option<usize> = None;
    loop {
        if search.stop_flag.load(Ordering::Relaxed) {
            return None;
        }
        let window_end = (window_start + TEXT_CHUNK_SIZE).min(len);
        if pending.is_empty() && !convert {
            // ASCII뿐이고(유발 글자·변환 대상 글자 없음) 원본 일치도 없는 창은 어느 줄에 속하든
            // 보고할 것이 없으므로 줄바꿈을 찾지 않고 건너뜁니다.
            let window = &buf[window_start..window_end];
            if is_ascii_chunk(window) {
                search.check_utf8(window_start, window_end, true);
                let search_end = (window_end + overlap).min(len);
                let has_hit = matcher
                    .raw_hits_in(&buf[search_from..search_end])
                    .next()
                    .is_some_and(|(start, _)| search_from + start < window_end);
                if !has_hit {
                    if window_end == len {
                        return Some(len);
                    }
                    skipped_from.get_or_insert(window_start);
                    window_start = window_end;
                    search_from = search_from.max(window_end);
                    continue;
                }
            }
            if let Some(from) = skipped_from.take() {
                // 건너뛴 구간에 줄바꿈이 있었다면 마지막 줄바꿈부터 일반 경로로 다시 처리합니다.
                // 없었다면 같은 긴 줄이 이어지는 것이므로 그대로 계속합니다.
                if let Some(index) = memchr::memrchr2(b'\n', b'\r', &buf[from..window_start]) {
                    return Some(from + index);
                }
            }
        }
        search.check_utf8(window_start, window_end, false);
        let line_break = memchr::memchr2(b'\n', b'\r', &buf[window_start..window_end]);
        let segment_end = line_break.map_or(window_end, |index| window_start + index);
        let line_ends = line_break.is_some() || window_end == len;
        if !convert && !is_ascii_chunk(&buf[window_start..segment_end]) {
            triggers.scan(buf, window_start, segment_end, |_| {
                convert = true;
                segment_end
            });
        }
        if !convert {
            let region_end = if line_ends {
                segment_end
            } else {
                (segment_end + overlap).min(len)
            };
            for (hit_start, hit_len) in matcher.raw_hits_in(&buf[search_from..region_end]) {
                let start = search_from + hit_start;
                if !line_ends && start >= segment_end {
                    break;
                }
                if textkey::variant_at(buf, start + hit_len) {
                    convert = true;
                    break;
                }
                pending.push((start, hit_len));
            }
            // 다음 창은 마지막 일치의 끝과 이 창의 끝 중 뒤쪽부터 찾습니다(같은 일치를 두 번 세지 않음).
            search_from = pending
                .last()
                .map_or(0, |&(start, length)| start + length)
                .max(segment_end);
        }
        if convert {
            // 줄 전체를 변환해 다시 셉니다(모은 원본 일치는 버림).
            let line_end = if line_ends {
                segment_end
            } else {
                memchr::memchr2(b'\n', b'\r', &buf[segment_end..])
                    .map_or(len, |index| segment_end + index)
            };
            // 변환한 줄 전체가 검색한 부분이므로 줄 끝까지 UTF-8 유효성을 확인합니다.
            search.check_utf8(window_end.min(line_end), line_end, false);
            search.counter.note_break_free(line_start, line_end);
            search.converted_line(line_start, line_end);
            return if search.full() { None } else { Some(line_end) };
        }
        if line_ends {
            // 줄 경계를 이미 알고 있으므로 줄 번호 계산과 줄 경계 찾기에서 줄을 다시 훑지 않습니다.
            search.counter.note_break_free(line_start, segment_end);
            search.line = Some((line_start, segment_end));
            search.line_no = None;
            for (hit_start, hit_len) in pending.drain(..) {
                if search.full() {
                    return None;
                }
                search.raw_hit(hit_start, hit_len);
            }
            return if search.full() {
                None
            } else {
                Some(segment_end)
            };
        }
        window_start = segment_end;
    }
}

/// `position`의 줄바꿈(LF·CRLF·CR)을 건너뛴 위치입니다. 줄바꿈이 없으면 그대로 돌려줍니다.
pub(crate) fn skip_line_break(buf: &[u8], position: usize) -> usize {
    match buf.get(position) {
        Some(b'\r') if buf.get(position + 1) == Some(&b'\n') => position + 2,
        Some(b'\r') | Some(b'\n') => position + 1,
        _ => position,
    }
}

/// `from`부터 다음 줄바꿈(CR 또는 LF)의 위치입니다. 없으면 끝 위치입니다.
pub(crate) fn next_line_break(buf: &[u8], from: usize) -> usize {
    memchr::memchr2(b'\n', b'\r', &buf[from.min(buf.len())..])
        .map_or(buf.len(), |index| from + index)
}

/// UTF-8 텍스트를 검색합니다. 해석하지 못한(올바른 UTF-8이 아닌) 바이트가 있었으면 참을 돌려줍니다.
pub(crate) fn search_utf8_text(
    buf: &[u8],
    matcher: &textkey::Matcher,
    is_exact: bool,
    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
    results: &mut Vec<RawMatch>,
) -> bool {
    let mut search = Utf8TextSearch {
        buf,
        matcher,
        is_exact,
        max_per_file,
        stop_flag,
        counter: textkey::LineCounter::new(),
        line: None,
        line_no: None,
        last_exact_line_start: None,
        last_raw_line: None,
        results,
        utf8_checked: 0,
        invalid_utf8: false,
    };
    match matcher.triggers() {
        Some(triggers) => search_utf8_with_triggers(&mut search, triggers),
        None => search_utf8_plain(&mut search),
    }
    // 판정은 실제로 검색한 부분만 합니다. 결과 상한·중지로 일찍 끝나면 읽지 않은 부분은 판정하지
    // 않습니다(디코딩 경로도 그 부분은 디코딩하지 않으므로 같은 규칙).
    search.invalid_utf8
}

/// 유발 글자가 없는 검색어(검색어의 `key`에 불변이 아닌 글자가 있는 경우)의 일반 텍스트 검색입니다.
pub(crate) fn search_utf8_plain(search: &mut Utf8TextSearch<'_>) {
    let buf = search.buf;
    let stop_flag = search.stop_flag;
    let ascii_pattern = search.matcher.pat_ascii();
    let mut chunk_start = 0usize;
    while chunk_start < buf.len() {
        if search.full() || stop_flag.load(Ordering::Relaxed) {
            return;
        }
        let mut chunk_end = (chunk_start + TEXT_CHUNK_SIZE).min(buf.len());
        if chunk_end < buf.len() {
            // 조각 끝을 줄바꿈 위치에 맞춥니다. 검색어에는 줄바꿈이 없으므로 일치가 조각에 걸치지 않습니다.
            let aligned = memchr::memchr2(b'\n', b'\r', &buf[chunk_end..])
                .map_or(buf.len(), |index| chunk_end + index);
            // 긴 줄에서는 그 사이에 줄바꿈이 없다는 것을 줄 번호 카운터에 알려 다시 세지 않게 합니다.
            if aligned - chunk_end > TEXT_CHUNK_SIZE {
                search.counter.note_break_free(chunk_end, aligned);
            }
            chunk_end = aligned;
        }
        let ascii = is_ascii_chunk(&buf[chunk_start..chunk_end]);
        search.check_utf8(chunk_start, chunk_end, ascii);
        if ascii {
            // ASCII 본문의 key는 ASCII뿐이므로 비ASCII 검색어는 일치할 수 없습니다.
            if ascii_pattern && search.raw_region(chunk_start, chunk_end) {
                return;
            }
        } else {
            let mut cursor = chunk_start;
            loop {
                let flagged = textkey::next_flagged(&buf[..chunk_end], cursor, ascii_pattern);
                let raw_end = flagged.map_or(chunk_end, |position| {
                    textkey::line_bounds(buf, position).0.max(cursor)
                });
                if search.raw_region(cursor, raw_end) {
                    return;
                }
                let Some(position) = flagged else {
                    break;
                };
                let (line_start, line_end) = textkey::line_bounds(buf, position);
                search.converted_line(line_start, line_end);
                if search.full() {
                    return;
                }
                cursor = line_end;
                if cursor >= chunk_end {
                    break;
                }
            }
        }
        chunk_start = chunk_end;
    }
}

/// 유발 글자 경로: 원본에서 `key(검색어)`를 찾고(단일 검색어라 빠른 사전 거르기가 유지됨),
/// ASCII가 아닌 조각에서만 유발 글자의 첫 바이트를 SIMD로 따로 찾습니다.
///
/// 유발 글자가 있는 줄과, 일치 바로 뒤에 변환 대상 글자가 붙은 줄만 `key()`로 바꿔 비교합니다.
pub(crate) fn search_utf8_with_triggers(
    search: &mut Utf8TextSearch<'_>,
    triggers: &textkey::Triggers,
) {
    let buf = search.buf;
    let matcher = search.matcher;
    let mut convert_lines: Vec<(usize, usize)> = Vec::new();
    let mut chunk_start = 0usize;
    while chunk_start < buf.len() {
        if search.full() || search.stop_flag.load(Ordering::Relaxed) {
            return;
        }
        let mut chunk_end = (chunk_start + TEXT_CHUNK_SIZE).min(buf.len());
        if chunk_end < buf.len() {
            // 조각 끝을 줄바꿈 위치에 맞춥니다. 검색어에는 줄바꿈이 없으므로 일치가 조각에 걸치지 않습니다.
            // 다음 줄바꿈은 한 조각 크기 안에서만 찾고, 없으면 긴 줄로 따로 처리합니다.
            let window_end = (chunk_end + TEXT_CHUNK_SIZE).min(buf.len());
            match memchr::memchr2(b'\n', b'\r', &buf[chunk_end..window_end]) {
                Some(index) => chunk_end += index,
                None if window_end == buf.len() => chunk_end = buf.len(),
                None => {
                    // 조각 안의 마지막 줄바꿈까지는 보통 조각으로 처리하고, 그 뒤의 긴 줄은 창 단위로 처리합니다.
                    match memchr::memrchr2(b'\n', b'\r', &buf[chunk_start + 1..chunk_end]) {
                        Some(index) => chunk_end = chunk_start + 1 + index,
                        None => {
                            let line_start = if matches!(buf[chunk_start], b'\n' | b'\r') {
                                skip_line_break(buf, chunk_start)
                            } else {
                                chunk_start
                            };
                            match search_long_line(search, triggers, line_start) {
                                Some(line_end) => {
                                    chunk_start = line_end;
                                    continue;
                                }
                                None => return,
                            }
                        }
                    }
                }
            }
        }
        convert_lines.clear();
        let ascii = is_ascii_chunk(&buf[chunk_start..chunk_end]);
        search.check_utf8(chunk_start, chunk_end, ascii);
        if ascii {
            // ASCII 조각에는 유발 글자도, 일치 뒤에 붙는 변환 대상 글자도 없으므로 줄 변환이 필요 없습니다.
            // 원본 일치를 바로 기록합니다(검색어의 key에 비ASCII가 있으면 일치할 수 없음).
            if search.matcher.pat_ascii() && search.raw_region(chunk_start, chunk_end) {
                return;
            }
            chunk_start = chunk_end;
            continue;
        }
        {
            triggers.scan(buf, chunk_start, chunk_end, |position| {
                let bounds = textkey::line_bounds(buf, position);
                convert_lines.push(bounds);
                bounds.1
            });
            convert_lines.sort_unstable();
            convert_lines.dedup();
        }
        let mut next_convert = 0usize;
        let mut converted_until = chunk_start;
        // 원본 일치는 바로 기록합니다(바로 뒤가 변환 대상 글자가 아닌 원본 일치는 그 자체로 참 일치).
        // 같은 줄의 나중 일치 때문에 줄을 변환하게 되면, 그 줄에서 이미 기록한 결과를 되돌리고
        // 줄 전체를 변환해 다시 셉니다(중복 방지). 현재 줄과 그 줄의 첫 결과 위치를 기억합니다.
        let mut current_line: Option<((usize, usize), usize)> = None;
        for (hit_start, hit_len) in matcher.raw_hits_in(&buf[chunk_start..chunk_end]) {
            let start = chunk_start + hit_start;
            if start < converted_until {
                continue;
            }
            // 이 일치보다 앞에서 끝나는 변환 대상 줄을 먼저 처리합니다(결과는 위치 순서).
            let before = convert_lines[next_convert..].partition_point(|line| line.1 < start);
            search.converted_lines(&convert_lines[next_convert..next_convert + before]);
            next_convert += before;
            if search.full() {
                return;
            }
            // 줄 번호 카운터를 먼저 옮겨, 줄 시작을 찾을 때 지나온 구간을 다시 훑지 않게 합니다.
            let (_, bounds) = search.locate(start);
            if current_line.is_none_or(|(line, _)| line != bounds) {
                current_line = Some((bounds, search.results.len()));
            }
            let in_trigger_line =
                next_convert < convert_lines.len() && convert_lines[next_convert].0 == bounds.0;
            if in_trigger_line || textkey::variant_at(buf, start + hit_len) {
                if in_trigger_line {
                    next_convert += 1;
                }
                // 이 줄에서 이미 기록한 원본 일치는 되돌리고 줄 전체를 변환해 다시 셉니다.
                if let Some((_, first_result)) = current_line.take() {
                    search.results.truncate(first_result);
                }
                search.converted_line(bounds.0, bounds.1);
                converted_until = bounds.1.max(start + 1);
            } else {
                search.raw_hit(start, hit_len);
            }
            if search.full() {
                return;
            }
            if search.results.len().is_multiple_of(1000) && search.stop_flag.load(Ordering::Relaxed)
            {
                return;
            }
        }
        search.converted_lines(&convert_lines[next_convert..]);
        if search.full() {
            return;
        }
        chunk_start = chunk_end;
    }
}

pub(crate) fn apply_match_limit(matches: Vec<RawMatch>, max_per_file: usize) -> Vec<RawMatch> {
    // 파일 단위 안내(종류가 메타데이터)는 실제 매치가 아닙니다.
    // 매치 상한 계산에서 제외하고, 잘림 이후에도 반드시 보존합니다. 종류는 만든 곳에서 정해 옵니다.
    // 안내 항목이 없고 상한 이하이면 그대로 돌려줍니다(새 배열을 만들지 않음).
    let has_metadata = matches.iter().any(|item| item.4.is_metadata());
    if !has_metadata && matches.len() <= max_per_file {
        return matches;
    }
    if !has_metadata {
        let mut matches = matches;
        matches.truncate(max_per_file);
        matches.push((
            0,
            MATCH_META_TRUNCATED.to_string(),
            None,
            None,
            ResultKind::Truncated,
        ));
        return matches;
    }
    let (mut visible_matches, mut metadata): (Vec<_>, Vec<_>) =
        matches.into_iter().partition(|item| !item.4.is_metadata());
    if visible_matches.len() > max_per_file {
        visible_matches.truncate(max_per_file);
        if !metadata.iter().any(|item| item.4 == ResultKind::Truncated) {
            metadata.insert(
                0,
                (
                    0,
                    MATCH_META_TRUNCATED.to_string(),
                    None,
                    None,
                    ResultKind::Truncated,
                ),
            );
        }
    }
    visible_matches.extend(metadata);
    visible_matches
}

/// 일반 텍스트 검색의 자동 인코딩 판정 결과입니다.
#[derive(Debug, PartialEq)]
pub(crate) enum TextEncoding {
    Known(&'static Encoding),
    /// UTF-8이 아니고 BOM·UTF-16 단서도 없어, CP949로 오류 없이 읽히는지 확인해야 합니다.
    /// 확인을 위한 디코딩과 검색을 한 번에 합니다(`search_korean_strict`).
    KoreanUnverified,
}

#[cfg(test)]
impl TextEncoding {
    /// 판정을 끝까지 마친 인코딩입니다(시험용).
    pub(crate) fn resolve(&self, data: &[u8]) -> &'static Encoding {
        match self {
            TextEncoding::Known(encoding) => encoding,
            TextEncoding::KoreanUnverified => detect_encoding(data),
        }
    }
}

pub(crate) fn detect_text_search_encoding(
    data: &[u8],
    pattern: &str,
    is_exact: bool,
) -> TextEncoding {
    let prefix = &data[..data.len().min(65536)];
    // Literal ASCII matches are byte-compatible with CP949. Keep the existing
    // sampled fast path; invalid decoded result snippets trigger a full recheck.
    // Exact/Unicode searches cannot use that proof and validate the full input.
    if !is_exact && pattern.is_ascii() {
        let mut end = prefix.len();
        while end < data.len() && end < prefix.len() + 3 && data[end] & 0xc0 == 0x80 {
            end += 1;
        }
        // 앞부분만 보므로 끝에서 잘린 CP949 두 바이트 글자는 CP949가 아니라는 근거로 쓰지 않습니다.
        let encoding = crate::utils::detect_encoding_with(&data[..end], end == data.len());
        // UTF-8 boundary extension may split a CP949 pair. Do not let that
        // artificial failure override a valid CP949 sample with UTF-16.
        if encoding == UTF_16LE || encoding == UTF_16BE {
            return TextEncoding::Known(crate::utils::detect_encoding_with(
                prefix,
                prefix.len() == data.len(),
            ));
        }
        return TextEncoding::Known(encoding);
    }
    match crate::utils::detect_until_korean(data) {
        crate::utils::Detection::Encoding(encoding) => TextEncoding::Known(encoding),
        crate::utils::Detection::KoreanCheck => TextEncoding::KoreanUnverified,
    }
}

/// CP949로 엄격하게(디코딩 오류가 나면 중단) 디코딩하면서 바로 검색합니다. 파일 전체가 오류 없이
/// 읽히면 "CP949로 판정한 뒤 검색"한 결과와 같고, 오류가 나면 None입니다(호출자가 나머지 판정을 이어 감).
/// 판정용 디코딩과 검색용 디코딩을 한 번으로 줄입니다.
pub(crate) fn search_korean_strict(
    mmap: &[u8],
    matcher: &textkey::Matcher,
    is_exact: bool,
    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
) -> Option<Vec<RawMatch>> {
    let mut results = Vec::new();
    let outcome = search_non_utf8_chunks(
        mmap,
        encoding_rs::EUC_KR,
        matcher,
        is_exact,
        stop_flag,
        max_per_file,
        &mut results,
        true,
    );
    if outcome == ChunkDecode::Aborted {
        return None;
    }
    // 결과 상한으로 일찍 끝났다면 나머지가 CP949로 읽히는지는 따로 확인합니다(중지는 그대로 둠).
    if results.len() > max_per_file
        && !stop_flag.load(Ordering::Relaxed)
        && !crate::utils::korean_bytes_valid(mmap, true)
    {
        return None;
    }
    Some(results)
}

/// 디코딩 경로 검색의 끝난 방식입니다.
#[derive(Debug, PartialEq)]
pub(crate) enum ChunkDecode {
    /// 끝까지(또는 상한·중지까지) 검색했습니다. `had_errors`는 해석하지 못한 바이트가 있었는지입니다.
    Completed { had_errors: bool },
    /// 엄격 모드에서 디코딩 오류를 만나 중단했습니다.
    Aborted,
}

/// 디코딩이 필요한 텍스트(UTF-16·CP949 등)를 청크 단위로 검색합니다.
/// 줄바꿈은 LF, CRLF, CR이며, 청크 끝의 CR은 다음 청크의 LF와 합쳐질 수 있으므로 기다립니다.
#[allow(clippy::too_many_arguments)]
pub(crate) fn search_non_utf8_chunks(
    mmap: &[u8],
    encoding: &'static Encoding,
    matcher: &textkey::Matcher,
    is_exact: bool,

    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
    results: &mut Vec<RawMatch>,
    strict: bool,
) -> ChunkDecode {
    const DECODE_CHUNK_SIZE: usize = 64 * 1024;
    let mut had_errors_seen = false;
    let mut decoder = encoding.new_decoder_without_bom_handling();
    let mut pending = String::new();
    let mut input_offset = 0usize;
    let mut line_number = 1usize;

    while input_offset < mmap.len() {
        if stop_flag.load(Ordering::Relaxed) {
            return ChunkDecode::Completed {
                had_errors: had_errors_seen,
            };
        }
        let end = (input_offset + DECODE_CHUNK_SIZE).min(mmap.len());
        let last = end == mmap.len();
        // encoding_rs는 출력 버퍼가 가득 차면 입력을 소비하지 않으므로 충분한
        // 청크 출력 공간을 미리 확보합니다. 필요 시 내부적으로 더 작은 결과를 생성합니다.
        let mut decoded = String::with_capacity((end - input_offset).saturating_mul(3));
        let (_status, consumed, had_errors) =
            decoder.decode_to_string(&mmap[input_offset..end], &mut decoded, last);
        if strict && (had_errors || (consumed == 0 && end > input_offset)) {
            // 엄격 모드: 이 인코딩으로 오류 없이 읽히지 않으므로 중단합니다(이 조각은 검색하지 않음).
            return ChunkDecode::Aborted;
        }
        had_errors_seen |= had_errors;
        if consumed == 0 && end > input_offset {
            // Decoder가 입력을 소비하지 못하는 경우 무한 루프를 방지합니다.
            break;
        }
        input_offset += consumed;
        let finished = input_offset >= mmap.len();
        pending.push_str(&decoded);

        // 완전한 줄까지만 검색합니다. 마지막 줄바꿈(CRLF는 CR부터)은 다음 조각으로 넘겨,
        // 조각 경계에 걸친 CRLF를 한 번만 세게 합니다.
        let bytes = pending.as_bytes();
        let mut cut = if finished {
            bytes.len()
        } else {
            match memchr::memrchr2(b'\n', b'\r', bytes) {
                Some(position)
                    if bytes[position] == b'\n' && position > 0 && bytes[position - 1] == b'\r' =>
                {
                    position - 1
                }
                Some(position) => position,
                None => 0,
            }
        };
        if cut > bytes.len() {
            cut = bytes.len();
        }
        if cut > 0 {
            if search_decoded_region(
                &bytes[..cut],
                &mut line_number,
                matcher,
                is_exact,
                stop_flag,
                max_per_file,
                results,
            ) {
                return ChunkDecode::Completed {
                    had_errors: had_errors_seen,
                };
            }
            pending.drain(..cut);
        }

        if consumed == 0 {
            break;
        }
    }

    if !pending.is_empty() && results.len() <= max_per_file && !stop_flag.load(Ordering::Relaxed) {
        let bytes = pending.as_bytes();
        search_decoded_region(
            bytes,
            &mut line_number,
            matcher,
            is_exact,
            stop_flag,
            max_per_file,
            results,
        );
    }
    ChunkDecode::Completed {
        had_errors: had_errors_seen,
    }
}

/// 디코딩한 텍스트 구간을 UTF-8 경로로 검색합니다. `line_number`는 구간 첫 줄의 번호이며,
/// 끝나면 구간 뒤 첫 줄의 번호로 바뀝니다. 디코딩한 위치는 원본 바이트 위치가 아니므로
/// 위치는 보고하지 않습니다(미리보기의 강조 위치는 유지). 상한을 넘거나 중지되면 참입니다.
pub(crate) fn search_decoded_region(
    region: &[u8],
    line_number: &mut usize,
    matcher: &textkey::Matcher,
    is_exact: bool,
    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
    results: &mut Vec<RawMatch>,
) -> bool {
    let mut found: Vec<RawMatch> = Vec::new();
    let budget = max_per_file.saturating_sub(results.len());
    search_utf8_text(region, matcher, is_exact, stop_flag, budget, &mut found);
    let base = *line_number - 1;
    for (line, content, _, _, kind) in found {
        results.push((base + line, content, None, None, kind));
    }
    *line_number += textkey::LineCounter::new().line_at(region, region.len()) - 1;
    results.len() > max_per_file || stop_flag.load(Ordering::Relaxed)
}

pub(crate) fn extract_line_content_bytes(
    mmap: &[u8],
    start: usize,
    end: usize,
    match_start: Option<usize>,
    match_len: Option<usize>,
) -> (String, ResultKind) {
    let mut line_end = end;
    if line_end > start && mmap[line_end - 1] == b'\r' {
        line_end -= 1;
    }
    let line_len = line_end - start;
    if line_len > 4096 {
        const PREVIEW_MAX_BYTES: usize = 1024;
        let mut preview_start = start;
        let mut preview_end = (start + PREVIEW_MAX_BYTES).min(line_end);

        // 긴 줄은 매치 위치 주변을 우선 표시하여 검색어가 미리보기에서 사라지지 않게 합니다.
        if let Some(hit_start) = match_start {
            let hit_start = hit_start.clamp(start, line_end);
            let hit_len = match_len
                .unwrap_or(0)
                .min(line_end.saturating_sub(hit_start));
            let desired_start = hit_start.saturating_sub(PREVIEW_MAX_BYTES / 2);
            preview_start = desired_start.max(start);
            preview_end = (preview_start + PREVIEW_MAX_BYTES).min(line_end);
            if preview_end < hit_start.saturating_add(hit_len) {
                preview_end = (hit_start.saturating_add(hit_len)).min(line_end);
                preview_start = preview_end.saturating_sub(PREVIEW_MAX_BYTES).max(start);
            }
        }

        // UTF-8 문자의 중간을 자르지 않도록 경계를 보정합니다.
        while preview_start > start && (mmap[preview_start] & 0xC0) == 0x80 {
            preview_start -= 1;
        }
        while preview_end < line_end && (mmap[preview_end] & 0xC0) == 0x80 {
            preview_end += 1;
        }

        let preview_bytes = &mmap[preview_start..preview_end];
        let preview_text = match simdutf8::basic::from_utf8(preview_bytes) {
            Ok(s) => s.trim().to_string(),
            Err(_) => String::from_utf8_lossy(preview_bytes).trim().to_string(),
        };
        let prefix = if preview_start > start { "..." } else { "" };
        let suffix = if preview_end < line_end { "..." } else { "" };
        return (
            format!("{MATCH_META_LONG_LINE_PREFIX}{prefix}{preview_text}{suffix}"),
            ResultKind::LongLine,
        );
    }
    let line_bytes = &mmap[start..line_end];
    let text = match simdutf8::basic::from_utf8(line_bytes) {
        Ok(s) => s.trim().to_string(),
        Err(_) => String::from_utf8_lossy(line_bytes).trim().to_string(),
    };
    (text, ResultKind::Match)
}

#[cfg(test)]
mod repeated_line_tests {
    use super::*;

    fn run(buf: &[u8]) -> Vec<RawMatch> {
        let matcher = textkey::Matcher::new("ab").unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let mut results = Vec::new();
        search_utf8_text(buf, &matcher, false, &stop, 1000, &mut results);
        results
    }

    /// 한 줄의 여러 일치는 같은 줄 내용과 각자의 위치를 보고합니다(7.4.6 줄 내용 재사용).
    #[test]
    fn repeated_hits_on_one_line_keep_their_offsets() {
        let results = run(b"x ab ab\r\nab cd ab\nzz");
        let got: Vec<_> = results
            .iter()
            .map(|(line, content, offset, length, kind)| {
                (*line, content.as_str(), *offset, *length, *kind)
            })
            .collect();
        assert_eq!(
            got,
            vec![
                (1, "x ab ab", Some(2), Some(2), ResultKind::Match),
                (1, "x ab ab", Some(5), Some(2), ResultKind::Match),
                (2, "ab cd ab", Some(9), Some(2), ResultKind::Match),
                (2, "ab cd ab", Some(15), Some(2), ResultKind::Match),
            ]
        );
    }

    /// 긴 줄은 일치마다 미리보기가 달라 재사용하지 않습니다.
    #[test]
    fn long_line_previews_follow_each_hit() {
        let mut line = "ab".to_string();
        line.push_str(&"x".repeat(6000));
        line.push_str("ab");
        let results = run(line.as_bytes());
        assert_eq!(results.len(), 2);
        assert!(results.iter().all(|item| item.4 == ResultKind::LongLine));
        assert_ne!(results[0].1, results[1].1);
    }

    #[test]
    fn match_limit_without_metadata_appends_notice_last() {
        let item = |n: usize| (n, format!("m{n}"), None, None, ResultKind::Match);
        let limited = apply_match_limit(vec![item(1), item(2), item(3)], 2);
        assert_eq!(limited.len(), 3);
        assert_eq!(limited[2].4, ResultKind::Truncated);
        let same = apply_match_limit(vec![item(1), item(2)], 2);
        assert_eq!(same.len(), 2);
    }
}
