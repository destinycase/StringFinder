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
    pat_upper: &'a str,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    mmap: &'a [u8],
    results: &'a mut Vec<RawMatch>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    offset_bonus: usize,
    last_offset: usize,
    last_line: usize,
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
    pattern: &str,
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

    let pat_upper = pattern.to_lowercase().to_uppercase();
    let mut ctx = XmlSearchContext {
        pat_upper: &pat_upper,
        ac,
        is_exact,
        mmap,
        results: &mut results,
        stop_flag,
        offset_bonus,
        last_offset: 0,
        last_line: 1,
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
        let is_match = if ctx.is_exact {
            trimmed.to_lowercase().to_uppercase() == ctx.pat_upper
        } else {
            ctx.ac.find(unescaped_text).is_some()
        };

        if is_match {
            if ctx.results.len() > ctx.max_per_file {
                return;
            }
            let mut match_offset = start_pos + ctx.offset_bonus;
            let mut match_len = raw_bytes.len();

            let range_end = (end_pos + ctx.offset_bonus).min(ctx.mmap.len());
            let search_start = start_pos + ctx.offset_bonus;
            if search_start < range_end {
                if let Some(m) = ctx.ac.find(&ctx.mmap[search_start..range_end]) {
                    match_offset = search_start + m.start();
                    match_len = m.len();
                }
            }

            // B5: 태그 경로는 이벤트 시 갱신된 캐시를 그대로 사용합니다.
            let tag_path = &ctx.tag_path_cache;

            // O(N*M) 방지: last_offset 이후부터 현재 매치 위치까지만 뉴라인 카운트
            if match_offset > ctx.last_offset {
                let count = ctx.mmap[ctx.last_offset..match_offset]
                    .iter()
                    .filter(|&&b| b == b'\n')
                    .count();
                ctx.last_line += count;
                ctx.last_offset = match_offset;
            }

            ctx.results.push((
                ctx.last_line,
                format!("/{}\t{}", tag_path, trimmed),
                if raw_bytes == unescaped_text.as_bytes() {
                    Some(match_offset)
                } else {
                    None
                },
                if raw_bytes == unescaped_text.as_bytes() {
                    Some(match_len)
                } else {
                    None
                },
            ));
        }
    }
}

fn process_xml_attributes(
    e: &quick_xml::events::BytesStart,
    start_pos: usize,
    ctx: &mut XmlSearchContext<'_>,
) -> Result<(), XmlSearchError> {
    for attr in e.attributes() {
        let attr = attr.map_err(XmlSearchError::parse)?;
        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
        let val = attr
            .unescape_value()
            .map_err(XmlSearchError::parse)?
            .into_owned();

        // Iteration and unescaping still validate attributes after collection stops.
        if ctx.results.len() > ctx.max_per_file {
            continue;
        }

        let is_match = if ctx.is_exact {
            val.trim().to_lowercase().to_uppercase() == ctx.pat_upper
        } else {
            ctx.ac.find(&val).is_some()
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
                if raw_value != val.as_bytes() {
                    return None;
                }
                ctx.ac
                    .find(raw_value)
                    .map(|matched| (offset + matched.start(), matched.len()))
            });
            let line_offset = location.map(|(offset, _)| offset).or(value_start);

            // O(N*M) 방지: last_offset 이후부터 현재 매치 위치까지만 뉴라인 카운트
            if let Some(offset) = line_offset.filter(|offset| *offset > ctx.last_offset) {
                let count = ctx.mmap[ctx.last_offset..offset]
                    .iter()
                    .filter(|&&b| b == b'\n')
                    .count();
                ctx.last_line += count;
                ctx.last_offset = offset;
            }

            ctx.results.push((
                if line_offset.is_some() {
                    ctx.last_line
                } else {
                    0
                },
                format!("/{}/@{}\t{}", ctx.tag_path_cache, key, val),
                location.map(|(offset, _)| offset),
                location.map(|(_, length)| length),
            ));
        }
    }
    Ok(())
}

pub fn check_xml_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Result<bool, XmlSearchError> {
    // UTF-8 BOM 스킵
    let parse_mmap = if mmap.starts_with(b"\xef\xbb\xbf") {
        &mmap[3..]
    } else {
        mmap
    };

    let mut reader = XmlReader::from_reader(parse_mmap);
    reader.trim_text(false);
    let pat_upper = pattern.to_lowercase().to_uppercase();
    let mut document = XmlDocumentState::new();
    let mut found = false;
    let mut pending_text: Cow<'_, str> = Cow::Borrowed("");

    loop {
        if stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let start_pos = reader.buffer_position();
        let event = reader.read_event();
        if !matches!(
            &event,
            Ok(Event::Text(_) | Event::CData(_) | Event::Comment(_) | Event::PI(_))
        ) {
            if !pending_text.trim().is_empty() {
                found |= if is_exact {
                    pending_text.trim().to_lowercase().to_uppercase() == pat_upper
                } else {
                    ac.find(pending_text.as_ref()).is_some()
                };
            }
            pending_text = Cow::Borrowed("");
        }
        match event {
            Err(error) => return Err(XmlSearchError::parse(error)),
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                document.on_start()?;
                found |= check_xml_attributes(&e, &pat_upper, ac, is_exact)?;
            }
            Ok(Event::Empty(e)) => {
                document.on_empty()?;
                found |= check_xml_attributes(&e, &pat_upper, ac, is_exact)?;
            }
            Ok(Event::End(_)) => {
                document.on_end()?;
            }
            Ok(Event::Text(e)) => {
                let text = e.unescape().map_err(XmlSearchError::parse)?;
                document.validate_text_outside_root(&text)?;
                if pending_text.is_empty() {
                    pending_text = text;
                } else {
                    pending_text.to_mut().push_str(&text);
                }
            }
            Ok(Event::CData(e)) => {
                let text = match e.into_inner() {
                    Cow::Borrowed(raw) => String::from_utf8_lossy(raw),
                    Cow::Owned(raw) => Cow::Owned(String::from_utf8_lossy(&raw).into_owned()),
                };
                document.validate_cdata()?;
                if pending_text.is_empty() {
                    pending_text = text;
                } else {
                    pending_text.to_mut().push_str(&text);
                }
            }
            Ok(Event::Decl(_)) => document.on_declaration(start_pos)?,
            Ok(Event::DocType(_)) => document.on_doctype()?,
            _ => (),
        }
    }
    if stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
        return Ok(false);
    }
    document.finish()?;
    Ok(found)
}

fn check_xml_attributes(
    e: &quick_xml::events::BytesStart,
    pat_upper: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
) -> Result<bool, XmlSearchError> {
    let mut found = false;
    for attr in e.attributes() {
        let attr = attr.map_err(XmlSearchError::parse)?;
        let val = attr
            .unescape_value()
            .map_err(XmlSearchError::parse)?
            .into_owned();
        if found {
            continue;
        }
        let is_match = if is_exact {
            val.trim().to_lowercase().to_uppercase() == pat_upper
        } else {
            ac.find(&val).is_some()
        };
        if is_match {
            found = true;
        }
    }
    Ok(found)
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
    fn adjacent_text_cdata_and_comments_use_one_value() {
        let ac = test_ac("needle");
        for xml in ["<r>nee<![CDATA[dle]]></r>", "<r>nee<!--note-->dle</r>"] {
            let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
            let rows =
                search_xml_file(xml.as_bytes(), "needle", &ac, false, stop.clone(), 10).unwrap();
            assert_eq!(rows.len(), 1);
            assert_eq!(rows[0].2, None);
            assert!(check_xml_file(xml.as_bytes(), "needle", &ac, false, stop).unwrap());
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
                assert!(
                    search_xml_file(xml.as_bytes(), "needle", &ac, false, stop.clone(), limit)
                        .is_err()
                );
            }
            assert!(check_xml_file(xml.as_bytes(), "needle", &ac, false, stop).is_err());
        }
    }

    #[test]
    fn empty_element_paths_include_the_element_and_restore_parent() {
        let ac = test_ac("needle");
        let xml = r#"<data><a x="needle"/><data x="needle"/>needle<끝 x="needle"/></data>"#;
        let matches = search_xml_file(
            xml.as_bytes(),
            "needle",
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
                "needle",
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

        assert!(search_xml_file(xml, "needle", &ac, false, stop_flag.clone(), 10).is_err());
        assert!(check_xml_file(xml, "needle", &ac, false, stop_flag).is_err());
    }

    #[test]
    fn xml_existence_check_validates_the_entire_document() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<root><value>needle</value></root><extra/>";

        assert!(check_xml_file(xml, "needle", &ac, false, stop_flag).is_err());
    }

    #[test]
    fn valid_xml_search_still_returns_matches() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<root>\n  <value>needle</value>\n</root>";

        let matches = search_xml_file(xml, "needle", &ac, false, stop_flag.clone(), 10).unwrap();

        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].0, 2);
        assert!(check_xml_file(xml, "needle", &ac, false, stop_flag).unwrap());
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
                check_xml_file(xml, "needle", &ac, false, stop_flag),
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
            search_xml_file(xml, "ACME", &ac, false, stop_flag.clone(), 10),
            Err(XmlSearchError::UnsupportedDtd(_))
        ));
        assert!(matches!(
            check_xml_file(xml, "ACME", &ac, false, stop_flag),
            Err(XmlSearchError::UnsupportedDtd(_))
        ));
    }

    #[test]
    fn doctype_after_root_is_a_parse_error() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<root>needle</root><!DOCTYPE root>";

        assert!(matches!(
            check_xml_file(xml, "needle", &ac, false, stop_flag),
            Err(XmlSearchError::Parse(_))
        ));
    }

    #[test]
    fn valid_xml_prolog_and_epilog_are_accepted() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let xml = b"<?xml version='1.0'?><!--before--><?check ok?><root>needle &amp; hay</root><!--after-->";

        let matches = search_xml_file(xml, "needle", &ac, false, stop_flag.clone(), 10).unwrap();

        assert_eq!(matches.len(), 1);
        assert!(check_xml_file(xml, "needle", &ac, false, stop_flag).unwrap());
    }
}
