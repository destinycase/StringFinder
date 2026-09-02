#![allow(dead_code)]
use crate::types::RawMatch;
use quick_xml::events::Event;
use quick_xml::reader::Reader as XmlReader;
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
    // B5: current_tags.join("/") 매 매치마다 재계산하는 비용을 없애기 위해 캐시합니다.
    tag_path_cache: String,
    max_per_file: usize,
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
    let mut buf = Vec::new();
    let mut current_tags = Vec::new();
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
        max_per_file,
    };

    loop {
        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let start_pos = reader.buffer_position();
        match reader.read_event_into(&mut buf) {
            Err(error) => return Err(XmlSearchError::parse(error)),
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                document.on_start()?;
                let end_pos = reader.buffer_position();
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                current_tags.push(name.clone());
                // B5: 태그 push 후 캐시 갱신
                ctx.tag_path_cache = current_tags.join("/");
                process_xml_attributes(&e, &name, start_pos, end_pos, &mut ctx)?;
            }
            Ok(Event::Empty(e)) => {
                document.on_empty()?;
                let end_pos = reader.buffer_position();
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                process_xml_attributes(&e, &name, start_pos, end_pos, &mut ctx)?;
            }
            Ok(Event::End(_)) => {
                document.on_end()?;
                current_tags.pop();
                // B5: 태그 pop 후 캐시 갱신
                ctx.tag_path_cache = current_tags.join("/");
            }
            Ok(Event::Text(e)) => {
                let end_pos = reader.buffer_position();
                let raw = e.as_ref();
                let text = e.unescape().map_err(XmlSearchError::parse)?;
                document.validate_text_outside_root(&text)?;
                process_xml_text_item(raw, &text, start_pos, end_pos, &mut ctx);
            }
            Ok(Event::CData(e)) => {
                let end_pos = reader.buffer_position();
                let raw = e.as_ref();
                let text = String::from_utf8_lossy(raw);
                document.validate_cdata()?;
                process_xml_text_item(raw, &text, start_pos, end_pos, &mut ctx);
            }
            Ok(Event::Decl(_)) => document.on_declaration(start_pos)?,
            Ok(Event::DocType(_)) => document.on_doctype()?,
            _ => (),
        }
        buf.clear();
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
                Some(match_offset),
                Some(match_len),
            ));
        }
    }
}

fn process_xml_attributes(
    e: &quick_xml::events::BytesStart,
    tag_name: &str,
    start_pos: usize,
    end_pos: usize,
    ctx: &mut XmlSearchContext<'_>,
) -> Result<(), XmlSearchError> {
    for attr in e.attributes() {
        let attr = attr.map_err(XmlSearchError::parse)?;
        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
        let val = attr
            .unescape_value()
            .map_err(XmlSearchError::parse)?
            .into_owned();

        let is_match = if ctx.is_exact {
            val.trim().to_lowercase().to_uppercase() == ctx.pat_upper
        } else {
            ctx.ac.find(&val).is_some()
        };

        if is_match {
            if ctx.results.len() > ctx.max_per_file {
                return Ok(());
            }
            let mut match_offset = start_pos + ctx.offset_bonus;
            let mut match_len = attr.value.len();

            let range_end = (end_pos + ctx.offset_bonus).min(ctx.mmap.len());
            let search_start = start_pos + ctx.offset_bonus;
            if search_start < range_end {
                if let Some(m) = ctx.ac.find(&ctx.mmap[search_start..range_end]) {
                    match_offset = search_start + m.start();
                    match_len = m.len();
                }
            }

            // O(N*M) 방지: last_offset 이후부터 현재 매치 위치까지만 뉴라인 카운트
            if match_offset > ctx.last_offset {
                let count = ctx.mmap[ctx.last_offset..match_offset]
                    .iter()
                    .filter(|&&b| b == b'\n')
                    .count();
                ctx.last_line += count;
                ctx.last_offset = match_offset;
            }

            // B5: ctx.tag_path_cache가 비어있으면 Empty 요소(부모 없음), 아니면 캐시 사용.
            let tag_path = if ctx.tag_path_cache.is_empty() {
                tag_name.to_string()
            } else if !ctx.tag_path_cache.ends_with(tag_name) {
                format!("{}/{}", ctx.tag_path_cache, tag_name)
            } else {
                ctx.tag_path_cache.clone()
            };

            ctx.results.push((
                ctx.last_line,
                format!("/{}/@{}\t{}", tag_path, key, val),
                Some(match_offset),
                Some(match_len),
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
    let mut buf = Vec::new();
    let pat_upper = pattern.to_lowercase().to_uppercase();
    let mut document = XmlDocumentState::new();
    let mut found = false;

    loop {
        if stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let start_pos = reader.buffer_position();
        match reader.read_event_into(&mut buf) {
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
                let trimmed = text.trim();
                document.validate_text_outside_root(&text)?;
                if !trimmed.is_empty() {
                    let is_match = if is_exact {
                        trimmed.to_lowercase().to_uppercase() == pat_upper
                    } else {
                        ac.find(text.as_ref()).is_some()
                    };
                    if is_match {
                        found = true;
                    }
                }
            }
            Ok(Event::CData(e)) => {
                let text = String::from_utf8_lossy(e.as_ref());
                let trimmed = text.trim();
                document.validate_cdata()?;
                if !trimmed.is_empty() {
                    let is_match = if is_exact {
                        trimmed.to_lowercase().to_uppercase() == pat_upper
                    } else {
                        ac.find(text.as_ref()).is_some()
                    };
                    if is_match {
                        found = true;
                    }
                }
            }
            Ok(Event::Decl(_)) => document.on_declaration(start_pos)?,
            Ok(Event::DocType(_)) => document.on_doctype()?,
            _ => (),
        }
        buf.clear();
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
    for attr in e.attributes() {
        let attr = attr.map_err(XmlSearchError::parse)?;
        let val = attr
            .unescape_value()
            .map_err(XmlSearchError::parse)?
            .into_owned();
        let is_match = if is_exact {
            val.trim().to_lowercase().to_uppercase() == pat_upper
        } else {
            ac.find(&val).is_some()
        };
        if is_match {
            return Ok(true);
        }
    }
    Ok(false)
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
