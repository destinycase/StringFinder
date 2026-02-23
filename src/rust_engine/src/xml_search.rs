#![allow(dead_code)]
use crate::types::SearchMatch;
use quick_xml::events::Event;
use quick_xml::reader::Reader as XmlReader;

struct XmlSearchContext<'a> {
    pat_lower: &'a str,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    mmap: &'a [u8],
    results: &'a mut Vec<SearchMatch>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    last_offset: usize,
    last_line: usize,
}

pub fn search_xml_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let mut reader = XmlReader::from_reader(mmap);
    reader.trim_text(false);
    let mut buf = Vec::new();
    let mut current_tags = Vec::new();

    let pat_lower = pattern.to_lowercase();
    let mut ctx = XmlSearchContext {
        pat_lower: &pat_lower,
        ac,
        is_exact,
        mmap,
        results: &mut results,
        stop_flag,
        last_offset: 0,
        last_line: 1,
    };

    loop {
        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let start_pos = reader.buffer_position();
        match reader.read_event_into(&mut buf) {
            Err(_) => break,
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                let end_pos = reader.buffer_position();
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                current_tags.push(name.clone());
                process_xml_attributes(&e, &name, start_pos, end_pos, &mut ctx);
            }
            Ok(Event::Empty(e)) => {
                let end_pos = reader.buffer_position();
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                process_xml_attributes(&e, &name, start_pos, end_pos, &mut ctx);
            }
            Ok(Event::End(_)) => {
                current_tags.pop();
            }
            Ok(Event::Text(e)) => {
                let end_pos = reader.buffer_position();
                let raw = e.as_ref();
                if let Ok(text) = e.unescape() {
                    process_xml_text_item(raw, &text, &current_tags, start_pos, end_pos, &mut ctx);
                }
            }
            Ok(Event::CData(e)) => {
                let end_pos = reader.buffer_position();
                let raw = e.as_ref();
                let text = String::from_utf8_lossy(raw);
                process_xml_text_item(raw, &text, &current_tags, start_pos, end_pos, &mut ctx);
            }
            _ => (),
        }
        buf.clear();
    }
    results
}

fn process_xml_text_item(
    raw_bytes: &[u8],
    unescaped_text: &str,
    current_tags: &[String],
    start_pos: usize,
    end_pos: usize,
    ctx: &mut XmlSearchContext<'_>,
) {
    let trimmed = unescaped_text.trim();
    if !trimmed.is_empty() {
        let is_match = if ctx.is_exact {
            trimmed.to_lowercase() == ctx.pat_lower
        } else {
            ctx.ac.find(unescaped_text).is_some()
        };

        if is_match {
            let mut match_offset = start_pos;
            let mut match_len = raw_bytes.len();

            let range_end = end_pos.min(ctx.mmap.len());
            if let Some(m) = ctx.ac.find(&ctx.mmap[start_pos..range_end]) {
                match_offset = start_pos + m.start();
                match_len = m.len();
            }

            let tag_path = current_tags.join("/");
            
            // O(N*M) 방지: last_offset 이후부터 현재 매치 위치까지만 뉴라인 카운트
            if match_offset > ctx.last_offset {
                let count = ctx.mmap[ctx.last_offset..match_offset].iter().filter(|&&b| b == b'\n').count();
                ctx.last_line += count;
                ctx.last_offset = match_offset;
            }
            
            ctx.results.push(SearchMatch::new(
                ctx.last_line,
                format!("/{} | {}", tag_path, trimmed),
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
) {
    for attr in e.attributes().flatten() {
        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
        let val = String::from_utf8_lossy(&attr.value).to_string();

        let is_match = if ctx.is_exact {
            val.to_lowercase() == ctx.pat_lower
        } else {
            ctx.ac.find(&val).is_some()
        };

        if is_match {
            let mut match_offset = start_pos;
            let mut match_len = attr.value.len();

            let range_end = end_pos.min(ctx.mmap.len());
            if let Some(m) = ctx.ac.find(&ctx.mmap[start_pos..range_end]) {
                match_offset = start_pos + m.start();
                match_len = m.len();
            }

            // O(N*M) 방지: last_offset 이후부터 현재 매치 위치까지만 뉴라인 카운트
            if match_offset > ctx.last_offset {
                let count = ctx.mmap[ctx.last_offset..match_offset].iter().filter(|&&b| b == b'\n').count();
                ctx.last_line += count;
                ctx.last_offset = match_offset;
            }

            ctx.results.push(SearchMatch::new(
                ctx.last_line,
                format!("<{}> @{} | {}", tag_name, key, val),
                Some(match_offset),
                Some(match_len),
            ));
        }
    }
}

pub fn check_xml_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> bool {
    let mut reader = XmlReader::from_reader(mmap);
    reader.trim_text(false);
    let mut buf = Vec::new();
    let pat_lower = pattern.to_lowercase();

    loop {
        if stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        match reader.read_event_into(&mut buf) {
            Err(_) => break,
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                if check_xml_attributes(&e, &pat_lower, ac, is_exact) {
                    return true;
                }
            }
            Ok(Event::Empty(e)) => {
                if check_xml_attributes(&e, &pat_lower, ac, is_exact) {
                    return true;
                }
            }
            Ok(Event::Text(e)) => {
                if let Ok(text) = e.unescape() {
                    let trimmed = text.trim();
                    if !trimmed.is_empty() {
                        let is_match = if is_exact {
                            trimmed.to_lowercase() == pat_lower
                        } else {
                            ac.find(text.as_ref()).is_some()
                        };
                        if is_match {
                            return true;
                        }
                    }
                }
            }
            Ok(Event::CData(e)) => {
                let text = String::from_utf8_lossy(e.as_ref());
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    let is_match = if is_exact {
                        trimmed.to_lowercase() == pat_lower
                    } else {
                        ac.find(text.as_ref()).is_some()
                    };
                    if is_match {
                        return true;
                    }
                }
            }
            _ => (),
        }
        buf.clear();
    }
    false
}

fn check_xml_attributes(
    e: &quick_xml::events::BytesStart,
    pat_lower: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
) -> bool {
    for attr in e.attributes().flatten() {
        let val = String::from_utf8_lossy(&attr.value).to_string();
        let is_match = if is_exact {
            val.to_lowercase() == pat_lower
        } else {
            ac.find(&val).is_some()
        };
        if is_match {
            return true;
        }
    }
    false
}
