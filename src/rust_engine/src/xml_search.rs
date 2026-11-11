use crate::types::RawMatch;
use quick_xml::events::Event;
use quick_xml::reader::Reader as XmlReader;
use std::borrow::Cow;
use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum XmlSearchError {
    Parse(String),
    UnsupportedDtd(String),
}

impl XmlSearchError {
    fn parse(detail: impl ToString) -> Self {
        Self::Parse(detail.to_string())
    }

    fn unsupported_dtd(detail: impl ToString) -> Self {
        Self::UnsupportedDtd(detail.to_string())
    }
}

impl fmt::Display for XmlSearchError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Parse(detail) | Self::UnsupportedDtd(detail) => formatter.write_str(detail),
        }
    }
}

impl std::error::Error for XmlSearchError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum XmlDocumentPhase {
    Prolog,
    Root,
    Epilog,
}

#[derive(Debug)]
struct XmlDocumentState {
    phase: XmlDocumentPhase,
    depth: usize,
    declaration_seen: bool,
}

impl XmlDocumentState {
    fn new() -> Self {
        Self {
            phase: XmlDocumentPhase::Prolog,
            depth: 0,
            declaration_seen: false,
        }
    }

    fn on_declaration(&mut self, start_pos: usize) -> Result<(), XmlSearchError> {
        if self.declaration_seen || self.phase != XmlDocumentPhase::Prolog || start_pos != 0 {
            return Err(XmlSearchError::parse(
                "XML declaration must appear exactly once at the beginning of the document",
            ));
        }
        self.declaration_seen = true;
        Ok(())
    }

    fn on_doctype(&self) -> Result<(), XmlSearchError> {
        if self.phase != XmlDocumentPhase::Prolog || self.depth != 0 {
            return Err(XmlSearchError::parse(
                "DOCTYPE declaration must appear before the root element",
            ));
        }
        Err(XmlSearchError::unsupported_dtd(
            "DTD declarations and entity expansion are not supported",
        ))
    }

    fn on_start(&mut self) -> Result<(), XmlSearchError> {
        if self.depth == 0 {
            match self.phase {
                XmlDocumentPhase::Prolog => self.phase = XmlDocumentPhase::Root,
                XmlDocumentPhase::Epilog => {
                    return Err(XmlSearchError::parse("multiple root elements"));
                }
                XmlDocumentPhase::Root => {}
            }
        }
        self.depth += 1;
        Ok(())
    }

    fn on_empty(&mut self) -> Result<(), XmlSearchError> {
        if self.depth == 0 {
            match self.phase {
                XmlDocumentPhase::Prolog => self.phase = XmlDocumentPhase::Epilog,
                XmlDocumentPhase::Epilog => {
                    return Err(XmlSearchError::parse("multiple root elements"));
                }
                XmlDocumentPhase::Root => {}
            }
        }
        Ok(())
    }

    fn on_end(&mut self) -> Result<(), XmlSearchError> {
        if self.depth == 0 {
            return Err(XmlSearchError::parse("unexpected closing element"));
        }
        self.depth -= 1;
        if self.depth == 0 {
            self.phase = XmlDocumentPhase::Epilog;
        }
        Ok(())
    }

    fn validate_text_outside_root(&self, text: &str) -> Result<(), XmlSearchError> {
        if self.depth == 0 && !text.trim().is_empty() {
            return Err(XmlSearchError::parse("text outside the root element"));
        }
        Ok(())
    }

    fn validate_cdata(&self) -> Result<(), XmlSearchError> {
        if self.depth == 0 {
            return Err(XmlSearchError::parse("CDATA outside the root element"));
        }
        Ok(())
    }

    fn finish(&self) -> Result<(), XmlSearchError> {
        if self.phase != XmlDocumentPhase::Epilog || self.depth != 0 {
            return Err(XmlSearchError::parse(
                "XML document is incomplete or has no root element",
            ));
        }
        Ok(())
    }
}

struct XmlSearchContext<'a> {
    matcher: &'a crate::textkey::Matcher,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    mmap: &'a [u8],
    results: &'a mut Vec<RawMatch>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    offset_bonus: usize,
    // 원본 기준 줄 번호(LF·CRLF·CR)입니다. 파서가 CR을 LF로 바꾼 텍스트로 세지 않습니다.
    line_counter: crate::textkey::LineCounter,
    // Append/truncate at UTF-8 boundaries instead of joining the full ancestry.
    tag_path_cache: String,
    path_lengths: Vec<usize>,
    max_per_file: usize,
    pending_text: Cow<'a, str>,
    pending_start: usize,
    pending_end: usize,
}

impl<'a> XmlSearchContext<'a> {
    fn append_text(&mut self, text: Cow<'a, str>, start: usize, end: usize) {
        if self.pending_text.is_empty() {
            self.pending_start = start;
            self.pending_text = text;
        } else {
            self.pending_text.to_mut().push_str(&text);
        }
        self.pending_end = end;
    }

    fn flush_text(&mut self) {
        if self.pending_text.is_empty() {
            return;
        }
        let text = std::mem::replace(&mut self.pending_text, Cow::Borrowed(""));
        let mmap = self.mmap;
        let raw =
            &mmap[self.pending_start + self.offset_bonus..self.pending_end + self.offset_bonus];
        process_xml_text_item(raw, &text, self.pending_start, self.pending_end, self);
    }

    fn push_tag(&mut self, name: &str) {
        self.path_lengths.push(self.tag_path_cache.len());
        if !self.tag_path_cache.is_empty() {
            self.tag_path_cache.push('/');
        }
        self.tag_path_cache.push_str(name);
    }

    fn pop_tag(&mut self) {
        if let Some(length) = self.path_lengths.pop() {
            self.tag_path_cache.truncate(length);
        }
    }
}

pub fn search_xml_file(
    mmap: &[u8],
    matcher: &crate::textkey::Matcher,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
) -> Result<Vec<RawMatch>, XmlSearchError> {
    let mut results = Vec::new();

    // UTF-8 BOM 스킵
    let mut offset_bonus = 0;
    let parse_mmap = if mmap.starts_with(b"\xef\xbb\xbf") {
        offset_bonus = 3;
        &mmap[3..]
    } else {
        mmap
    };

    let mut reader = XmlReader::from_reader(parse_mmap);
    reader.trim_text(false);
    let mut document = XmlDocumentState::new();

    let mut ctx = XmlSearchContext {
        matcher,
        ac,
        is_exact,
        mmap,
        results: &mut results,
        stop_flag,
        offset_bonus,
        line_counter: crate::textkey::LineCounter::new(),
        tag_path_cache: String::new(),
        path_lengths: Vec::new(),
        max_per_file,
        pending_text: Cow::Borrowed(""),
        pending_start: 0,
        pending_end: 0,
    };

    loop {
        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let start_pos = reader.buffer_position();
        let event = reader.read_event();
        if !matches!(
            &event,
            Ok(Event::Text(_) | Event::CData(_) | Event::Comment(_) | Event::PI(_))
        ) {
            ctx.flush_text();
        }
        match event {
            Err(error) => return Err(XmlSearchError::parse(error)),
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                document.on_start()?;
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                ctx.push_tag(&name);
                process_xml_attributes(&e, start_pos, &mut ctx)?;
            }
            Ok(Event::Empty(e)) => {
                document.on_empty()?;
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                ctx.push_tag(&name);
                process_xml_attributes(&e, start_pos, &mut ctx)?;
                ctx.pop_tag();
            }
            Ok(Event::End(_)) => {
                document.on_end()?;
                ctx.pop_tag();
            }
            Ok(Event::Text(e)) => {
                let end_pos = reader.buffer_position();
                let text = e.unescape().map_err(XmlSearchError::parse)?;
                document.validate_text_outside_root(&text)?;
                ctx.append_text(text, start_pos, end_pos);
            }
            Ok(Event::CData(e)) => {
                let end_pos = reader.buffer_position();
                let text = match e.into_inner() {
                    Cow::Borrowed(raw) => String::from_utf8_lossy(raw),
                    Cow::Owned(raw) => Cow::Owned(String::from_utf8_lossy(&raw).into_owned()),
                };
                document.validate_cdata()?;
                ctx.append_text(text, start_pos, end_pos);
            }
            Ok(Event::Decl(_)) => document.on_declaration(start_pos)?,
            Ok(Event::DocType(_)) => document.on_doctype()?,
            _ => (),
        }
    }
    if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    document.finish()?;
    drop(ctx);
    Ok(results)
}

fn process_xml_text_item(
    raw_bytes: &[u8],
    unescaped_text: &str,
    start_pos: usize,
    end_pos: usize,
    ctx: &mut XmlSearchContext<'_>,
) {
    let trimmed = unescaped_text.trim();
    if !trimmed.is_empty() {
        // 모든 형식이 같은 비교 기준(textkey)을 씁니다. 정확히 일치도 NFC를 적용합니다.
        let is_match = if ctx.is_exact {
            ctx.matcher.equals_trimmed(unescaped_text)
        } else {
            ctx.matcher.contains(unescaped_text)
        };

        if is_match {
            if ctx.results.len() > ctx.max_per_file {
                return;
            }
            let range_end = (end_pos + ctx.offset_bonus).min(ctx.mmap.len());
            let search_start = (start_pos + ctx.offset_bonus).min(range_end);
            let raw_range = &ctx.mmap[search_start..range_end];

            // 원본과 같은 바이트이고 변환 없이 비교되는 값만 위치를 보고합니다(틀린 위치 금지).
            let location = if raw_bytes == unescaped_text.as_bytes()
                && ctx.matcher.raw_positions_valid(unescaped_text)
            {
                ctx.ac
                    .find(raw_range)
                    .map(|m| (search_start + m.start(), m.len()))
            } else {
                None
            };

            // 여러 줄에 걸친 값은 값이 시작하는 줄(앞 공백을 뺀 첫 글자)을 보고합니다.
            let value_start = search_start
                + raw_range
                    .iter()
                    .position(|b| !matches!(b, b' ' | b'\t' | b'\r' | b'\n'))
                    .unwrap_or(0);
            let line = ctx.line_counter.line_at(ctx.mmap, value_start);

            // B5: 태그 경로는 이벤트 시 갱신된 캐시를 그대로 사용합니다.
            let tag_path = &ctx.tag_path_cache;
            ctx.results.push((
                line,
                format!("/{}\t{}", tag_path, trimmed),
                location.map(|(offset, _)| offset),
                location.map(|(_, length)| length),
            ));
        }
    }
}

fn attribute_value(
    attr: &quick_xml::events::attributes::Attribute<'_>,
) -> Result<String, XmlSearchError> {
    let raw = std::str::from_utf8(attr.value.as_ref()).map_err(XmlSearchError::parse)?;
    if !raw.bytes().any(|b| matches!(b, b'\t' | b'\r' | b'\n')) {
        return attr
            .unescape_value()
            .map(|v| v.into_owned())
            .map_err(XmlSearchError::parse);
    }
    // XML normalizes literal attribute whitespace BEFORE expanding references.
    // Thus &#xA; remains a newline, whereas a literal CRLF becomes one space.
    let mut normalized = String::with_capacity(raw.len());
    let mut chars = raw.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\r' => {
                if chars.peek() == Some(&'\n') {
                    chars.next();
                }
                normalized.push(' ');
            }
            '\n' | '\t' => normalized.push(' '),
            _ => normalized.push(c),
        }
    }
    quick_xml::escape::unescape(&normalized)
        .map(|v| v.into_owned())
        .map_err(XmlSearchError::parse)
}

fn process_xml_attributes(
    e: &quick_xml::events::BytesStart,
    start_pos: usize,
    ctx: &mut XmlSearchContext<'_>,
) -> Result<(), XmlSearchError> {
    for attr in e.attributes() {
        let attr = attr.map_err(XmlSearchError::parse)?;
        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
        let val = attribute_value(&attr)?;

        // Iteration and unescaping still validate attributes after collection stops.
        if ctx.results.len() > ctx.max_per_file {
            continue;
        }

        let is_match = if ctx.is_exact {
            ctx.matcher.equals_trimmed(&val)
        } else {
            ctx.matcher.contains(&val)
        };

        if is_match {
            // quick-xml 0.31 attributes borrow slices from BytesStart's buffer.
            // Validate the slice range before translating past the opening '<'.
            let raw_value = attr.value.as_ref();
            let tag_bytes: &[u8] = e.as_ref();
            let relative = (raw_value.as_ptr() as usize).checked_sub(tag_bytes.as_ptr() as usize);
            let value_start = relative
                .filter(|offset| offset.saturating_add(raw_value.len()) <= tag_bytes.len())
                .and_then(|offset| start_pos.checked_add(1 + ctx.offset_bonus + offset))
                .filter(|offset| {
                    ctx.mmap
                        .get(*offset..offset.saturating_add(raw_value.len()))
                        == Some(raw_value)
                });
            let location = value_start.and_then(|offset| {
                // Entity expansion can make raw and decoded matches unrelated.
                // 변환해야 비교되는 값도 원본 위치를 알 수 없으므로 생략합니다.
                if raw_value != val.as_bytes() || !ctx.matcher.raw_positions_valid(&val) {
                    return None;
                }
                ctx.ac
                    .find(raw_value)
                    .map(|matched| (offset + matched.start(), matched.len()))
            });
            // 속성 값은 값이 시작하는 줄을 보고합니다.
            let line_offset = value_start.or(location.map(|(offset, _)| offset));
            let line = line_offset.map(|offset| ctx.line_counter.line_at(ctx.mmap, offset));

            ctx.results.push((
                line.unwrap_or(0),
                format!("/{}/@{}\t{}", ctx.tag_path_cache, key, val),
                location.map(|(offset, _)| offset),
                location.map(|(_, length)| length),
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use aho_corasick::AhoCorasickBuilder;

    fn test_ac(pattern: &str) -> aho_corasick::AhoCorasick {
        AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .build([pattern])
            .unwrap()
    }

    #[test]
    fn literal_attribute_whitespace_normalizes_before_references() {
        let ac = test_ac("needle value");
        for whitespace in ["\t", "\n", "\r", "\r\n", "&#xA;"] {
            let xml = format!("<r v=\"needle{whitespace}value\"/>");
            let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
            let expected = usize::from(whitespace != "&#xA;");
            let rows = search_xml_file(
                xml.as_bytes(),
                &crate::textkey::Matcher::new("needle value").unwrap(),
                &ac,
                false,
                stop.clone(),
                10,
            )
            .unwrap();
            assert_eq!(rows.len(), expected);
            assert_eq!(
                !search_xml_file(
                    xml.as_bytes(),
                    &crate::textkey::Matcher::new("needle value").unwrap(),
                    &ac,
                    false,
                    stop,
                    10
                )
                .unwrap()
                .is_empty(),
                expected == 1
            );
            if expected == 1 {
                assert_eq!(rows[0].2, None);
            }
        }
    }

    #[test]
    fn adjacent_text_cdata_and_comments_use_one_value() {
        let ac = test_ac("needle");
        for xml in ["<r>nee<![CDATA[dle]]></r>", "<r>nee<!--note-->dle</r>"] {
            let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
            let rows = search_xml_file(
                xml.as_bytes(),
                &crate::textkey::Matcher::new("needle").unwrap(),
                &ac,
                false,
                stop.clone(),
                10,
            )
            .unwrap();
            assert_eq!(rows.len(), 1);
            assert_eq!(rows[0].2, None);
            assert!(!search_xml_file(
                xml.as_bytes(),
                &crate::textkey::Matcher::new("needle").unwrap(),
                &ac,
                false,
                stop,
                10
            )
            .unwrap()
            .is_empty());
        }
    }

    #[test]
    fn attribute_validation_continues_after_result_limit() {
        let ac = test_ac("needle");
        for tail in [
            r#"<c x="needle" x="duplicate"/>"#,
            r#"<c x="needle" y="&unknown;"/>"#,
            r#"<c x="needle" broken/>"#,
        ] {
            let xml = format!("<root><a>needle</a><b>needle</b>{tail}</root>");
            let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
            for limit in [1, 10] {
                assert!(search_xml_file(
                    xml.as_bytes(),
                    &crate::textkey::Matcher::new("needle").unwrap(),
                    &ac,
                    false,
                    stop.clone(),
                    limit
                )
                .is_err());
            }
            assert!(search_xml_file(
                xml.as_bytes(),
                &crate::textkey::Matcher::new("needle").unwrap(),
                &ac,
                false,
                stop,
                10
            )
            .is_err());
        }
    }

    #[test]
    fn empty_element_paths_include_the_element_and_restore_parent() {
        let ac = test_ac("needle");
        let xml = r#"<data><a x="needle"/><data x="needle"/>needle<끝 x="needle"/></data>"#;
        let matches = search_xml_file(
            xml.as_bytes(),
            &crate::textkey::Matcher::new("needle").unwrap(),
            &ac,
            false,
            std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
            10,
        )
        .unwrap();
        let contents: Vec<_> = matches.iter().map(|m| m.1.as_str()).collect();
        assert_eq!(
            contents,
            [
                "/data/a/@x\tneedle",
                "/data/data/@x\tneedle",
                "/data\tneedle",
                "/data/끝/@x\tneedle"
            ]
        );
    }

    #[test]
    fn attribute_locations_refer_to_the_matching_value() {
        let ac = test_ac("needle");
        for prefix in ["", "\u{feff}"] {
            let xml = format!(
                "{prefix}<root needle=\"other\"\n x='needle' y=\"needle\" z=\"nee&#100;le\"/>"
            );
            let matches = search_xml_file(
                xml.as_bytes(),
                &crate::textkey::Matcher::new("needle").unwrap(),
                &ac,
                false,
                std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
                10,
            )
            .unwrap();
            assert_eq!(matches.len(), 3);
            assert_eq!(matches[0].0, 2);
            assert_eq!(matches[0].2, Some(xml.find("'needle'").unwrap() + 1));
            assert_eq!(matches[1].2, Some(xml.find("y=\"needle").unwrap() + 3));
            assert_eq!((matches[2].2, matches[2].3), (None, None));
        }
    }

    #[test]
    fn malformed_xml_is_an_error_even_when_text_matches() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<root><value>needle</root>";

        assert!(search_xml_file(
            xml,
            &crate::textkey::Matcher::new("needle").unwrap(),
            &ac,
            false,
            stop_flag.clone(),
            10
        )
        .is_err());
        assert!(search_xml_file(
            xml,
            &crate::textkey::Matcher::new("needle").unwrap(),
            &ac,
            false,
            stop_flag,
            10
        )
        .is_err());
    }

    #[test]
    fn xml_search_validates_the_entire_document() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<root><value>needle</value></root><extra/>";

        assert!(search_xml_file(
            xml,
            &crate::textkey::Matcher::new("needle").unwrap(),
            &ac,
            false,
            stop_flag,
            10
        )
        .is_err());
    }

    #[test]
    fn valid_xml_search_still_returns_matches() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<root>\n  <value>needle</value>\n</root>";

        let matches = search_xml_file(
            xml,
            &crate::textkey::Matcher::new("needle").unwrap(),
            &ac,
            false,
            stop_flag.clone(),
            10,
        )
        .unwrap();

        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].0, 2);
        assert!(!search_xml_file(
            xml,
            &crate::textkey::Matcher::new("needle").unwrap(),
            &ac,
            false,
            stop_flag,
            10
        )
        .unwrap()
        .is_empty());
    }

    #[test]
    fn xml_declaration_is_only_allowed_at_the_beginning() {
        let ac = test_ac("needle");
        let malformed_documents: [&[u8]; 2] = [
            b"<root>needle</root><?xml version='1.0'?>",
            b" <!--comment--><?xml version='1.0'?><root>needle</root>",
        ];

        for xml in malformed_documents {
            let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
            assert!(matches!(
                search_xml_file(
                    xml,
                    &crate::textkey::Matcher::new("needle").unwrap(),
                    &ac,
                    false,
                    stop_flag,
                    10
                ),
                Err(XmlSearchError::Parse(_))
            ));
        }
    }

    #[test]
    fn dtd_is_reported_as_explicitly_unsupported() {
        let ac = test_ac("ACME");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<!DOCTYPE root [<!ENTITY company 'ACME'>]><root>&company;</root>";

        assert!(matches!(
            search_xml_file(
                xml,
                &crate::textkey::Matcher::new("ACME").unwrap(),
                &ac,
                false,
                stop_flag.clone(),
                10
            ),
            Err(XmlSearchError::UnsupportedDtd(_))
        ));
        assert!(matches!(
            search_xml_file(
                xml,
                &crate::textkey::Matcher::new("ACME").unwrap(),
                &ac,
                false,
                stop_flag,
                10
            ),
            Err(XmlSearchError::UnsupportedDtd(_))
        ));
    }

    #[test]
    fn doctype_after_root_is_a_parse_error() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<root>needle</root><!DOCTYPE root>";

        assert!(matches!(
            search_xml_file(
                xml,
                &crate::textkey::Matcher::new("needle").unwrap(),
                &ac,
                false,
                stop_flag,
                10
            ),
            Err(XmlSearchError::Parse(_))
        ));
    }

    #[test]
    fn valid_xml_prolog_and_epilog_are_accepted() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<?xml version='1.0'?><!--before--><?check ok?><root>needle &amp; hay</root><!--after-->";

        let matches = search_xml_file(
            xml,
            &crate::textkey::Matcher::new("needle").unwrap(),
            &ac,
            false,
            stop_flag.clone(),
            10,
        )
        .unwrap();

        assert_eq!(matches.len(), 1);
        assert!(!search_xml_file(
            xml,
            &crate::textkey::Matcher::new("needle").unwrap(),
            &ac,
            false,
            stop_flag,
            10
        )
        .unwrap()
        .is_empty());
    }
}
