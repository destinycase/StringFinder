#![allow(dead_code)]
use crate::types::RawMatch;
use quick_xml::events::Event;
use quick_xml::reader::Reader as XmlReader;

struct XmlSearchContext<'a> {
    pat_upper: &'a str,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    mmap: &'a [u8],
    results: &'a mut Vec<RawMatch>,
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
) -> Vec<RawMatch> {
    let mut results = Vec::new();
    
    // UTF-8 BOM 스킵
    let parse_mmap = if mmap.starts_with(b"\xef\xbb\xbf") {
        &mmap[3..]
    } else {
        mmap
    };

    let mut reader = XmlReader::from_reader(parse_mmap);
    reader.trim_text(false);
    let mut buf = Vec::new();
    let mut current_tags = Vec::new();

    let pat_upper = pattern.to_lowercase().to_uppercase();
    let mut ctx = XmlSearchContext {
        pat_upper: &pat_upper,
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
                process_xml_attributes(&e, &name, &current_tags, start_pos, end_pos, &mut ctx);
            }
            Ok(Event::Empty(e)) => {
                let end_pos = reader.buffer_position();
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                process_xml_attributes(&e, &name, &current_tags, start_pos, end_pos, &mut ctx);
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
            trimmed.to_lowercase().to_lowercase().to_uppercase() == ctx.pat_upper
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
    current_tags: &[String],
    start_pos: usize,
    end_pos: usize,
    ctx: &mut XmlSearchContext<'_>,
) {
    for attr in e.attributes().flatten() {
        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
        let val = String::from_utf8_lossy(&attr.value).to_string();

        let is_match = if ctx.is_exact {
            val.to_lowercase().to_lowercase().to_uppercase() == ctx.pat_upper
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

            let tag_path = if current_tags.is_empty() || current_tags.last() != Some(&tag_name.to_string()) {
                if current_tags.is_empty() {
                    tag_name.to_string()
                } else {
                    format!("{}/{}", current_tags.join("/"), tag_name)
                }
            } else {
                current_tags.join("/")
            };

            ctx.results.push((
                ctx.last_line,
                format!("/{}/@{}\t{}", tag_path, key, val),
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

    loop {
        if stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        match reader.read_event_into(&mut buf) {
            Err(_) => break,
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                if check_xml_attributes(&e, &pat_upper, ac, is_exact) {
                    return true;
                }
            }
            Ok(Event::Empty(e)) => {
                if check_xml_attributes(&e, &pat_upper, ac, is_exact) {
                    return true;
                }
            }
            Ok(Event::Text(e)) => {
                if let Ok(text) = e.unescape() {
                    let trimmed = text.trim();
                    if !trimmed.is_empty() {
                        let is_match = if is_exact {
                            trimmed.to_lowercase().to_lowercase().to_uppercase() == pat_upper
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
                        trimmed.to_lowercase().to_uppercase() == pat_upper
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
    pat_upper: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
) -> bool {
    for attr in e.attributes().flatten() {
        let val = String::from_utf8_lossy(&attr.value).to_string();
        let is_match = if is_exact {
            val.to_lowercase().to_lowercase().to_uppercase() == pat_upper
        } else {
            ac.find(&val).is_some()
        };
        if is_match {
            return true;
        }
    }
    false
}
