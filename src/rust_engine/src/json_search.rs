use serde::de::{DeserializeSeed, IgnoredAny, MapAccess, SeqAccess, Visitor};
use serde::Deserialize;
use serde_json::Deserializer;
use std::fmt;

use crate::types::RawMatch;

enum PathComponent {
    ObjectKey(String),
    ArrayIndex(usize),
}

struct JsonSearchState<'data> {
    pattern_upper: String,
    ac: &'data aho_corasick::AhoCorasick,
    is_exact: bool,
    mmap: &'data [u8],
    last_offset: usize,
    last_line: usize,
    path: Vec<PathComponent>,
    results: Vec<RawMatch>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
    collect_results: bool,
    found: bool,
    valid: bool,
}

impl<'data> JsonSearchState<'data> {
    #[allow(clippy::too_many_arguments)]
    fn new(
        mmap: &'data [u8],
        pattern: &str,
        ac: &'data aho_corasick::AhoCorasick,
        is_exact: bool,
        stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
        max_per_file: usize,
        max_json_depth: usize,
        collect_results: bool,
    ) -> Self {
        Self {
            pattern_upper: pattern.to_lowercase().to_uppercase(),
            ac,
            is_exact,
            mmap,
            last_offset: 0,
            last_line: 1,
            path: Vec::new(),
            results: Vec::new(),
            stop_flag,
            max_per_file,
            max_json_depth,
            collect_results,
            found: false,
            valid: true,
        }
    }

    fn path_string(&self) -> String {
        let mut path = String::new();
        for component in &self.path {
            path.push('/');
            match component {
                PathComponent::ObjectKey(key) => path.push_str(key),
                PathComponent::ArrayIndex(index) => path.push_str(&index.to_string()),
            }
        }
        path
    }

    fn record_scalar(&mut self, value: &str) {
        if self.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            return;
        }

        let is_match = if self.is_exact {
            crate::utils::normalize_unicode(value).trim().to_lowercase().to_uppercase()
                == self.pattern_upper
        } else {
            self.ac.find(value).is_some()
        };
        if !is_match {
            return;
        }
        self.found = true;
        if !self.collect_results || self.results.len() > self.max_per_file {
            return;
        }

        let mut found_pos = None;
        let mut found_len = 0;
        if self.last_offset < self.mmap.len() {
            if let Some(m) = self.ac.find(&self.mmap[self.last_offset..]) {
                found_pos = Some(self.last_offset + m.start());
                found_len = m.len();
            }
        }

        let path_string = self.path_string();
        if let Some(actual_pos) = found_pos {
            self.last_line +=
                memchr::memchr_iter(b'\n', &self.mmap[self.last_offset..actual_pos]).count();
            self.results.push((
                self.last_line,
                format!("{}\t{}", path_string, value),
                Some(actual_pos),
                Some(found_len),
            ));
            self.last_offset = actual_pos + found_len;
        } else {
            self.results.push((
                self.last_line,
                format!("{}\t{}", path_string, value),
                None,
                None,
            ));
        }
    }
}

struct JsonSeed<'state, 'data> {
    state: &'state mut JsonSearchState<'data>,
    depth: usize,
}

impl<'de, 'state, 'data> DeserializeSeed<'de> for JsonSeed<'state, 'data> {
    type Value = ();

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        if self.depth > self.state.max_json_depth {
            IgnoredAny::deserialize(deserializer).map(|_| ())
        } else {
            deserializer.deserialize_any(JsonVisitor {
                state: self.state,
                depth: self.depth,
            })
        }
    }
}

struct JsonVisitor<'state, 'data> {
    state: &'state mut JsonSearchState<'data>,
    depth: usize,
}

impl<'de, 'state, 'data> Visitor<'de> for JsonVisitor<'state, 'data> {
    type Value = ();

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON value")
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        while let Some(key) = map.next_key::<String>()? {
            if self.state.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                break;
            }
            self.state.path.push(PathComponent::ObjectKey(key));
            map.next_value_seed(JsonSeed {
                state: &mut *self.state,
                depth: self.depth.saturating_add(1),
            })?;
            self.state.path.pop();
        }
        Ok(())
    }

    fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut index = 0;
        while !self.state.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            self.state.path.push(PathComponent::ArrayIndex(index));
            let element = seq.next_element_seed(JsonSeed {
                state: &mut *self.state,
                depth: self.depth.saturating_add(1),
            })?;
            self.state.path.pop();
            if element.is_none() {
                break;
            }
            index = index.saturating_add(1);
        }
        Ok(())
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar(value);
        Ok(())
    }

    fn visit_borrowed_str<E>(self, value: &'de str) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar(value);
        Ok(())
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar(&value);
        Ok(())
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar(&value.to_string());
        Ok(())
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar(&value.to_string());
        Ok(())
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar(&value.to_string());
        Ok(())
    }

    fn visit_bool<E>(self, _value: bool) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(())
    }
}

#[allow(clippy::too_many_arguments)]
fn parse_json<'data>(
    mmap: &'data [u8],
    pattern: &'data str,
    ac: &'data aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
    collect_results: bool,
) -> JsonSearchState<'data> {
    let parse_mmap = if mmap.starts_with(b"\xef\xbb\xbf") {
        &mmap[3..]
    } else {
        mmap
    };

    let mut state = JsonSearchState::new(
        parse_mmap,
        pattern,
        ac,
        is_exact,
        stop_flag,
        max_per_file,
        max_json_depth,
        collect_results,
    );
    let mut deserializer = Deserializer::from_slice(parse_mmap);
    let parse_result = JsonSeed {
        state: &mut state,
        depth: 0,
    }
    .deserialize(&mut deserializer);
    let stopped = state.stop_flag.load(std::sync::atomic::Ordering::Relaxed);
    state.valid = if stopped {
        parse_result.is_ok()
    } else {
        parse_result.is_ok() && deserializer.end().is_ok()
    };
    state
}

pub fn search_json_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
) -> Vec<RawMatch> {
    if !is_exact && ac.find(mmap).is_none() {
        return Vec::new();
    }

    let mut state = parse_json(
        mmap,
        pattern,
        ac,
        is_exact,
        stop_flag,
        max_per_file,
        max_json_depth,
        true,
    );
    if state.valid {
        std::mem::take(&mut state.results)
    } else {
        Vec::new()
    }
}

pub fn check_json_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_json_depth: usize,
) -> bool {
    if !is_exact && ac.find(mmap).is_none() {
        return false;
    }

    let state = parse_json(
        mmap,
        pattern,
        ac,
        is_exact,
        stop_flag,
        0,
        max_json_depth,
        false,
    );
    state.valid && state.found
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
    fn search_json_honors_nesting_depth_limit() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = br#"{"outer":{"key":"needle"}}"#;

        assert_eq!(search_json_file(json, "needle", &ac, false, stop_flag.clone(), 10, 2).len(), 1);
        assert!(search_json_file(json, "needle", &ac, false, stop_flag.clone(), 10, 1).is_empty());
        assert!(!check_json_file(json, "needle", &ac, false, stop_flag.clone(), 1));
        assert!(check_json_file(json, "needle", &ac, false, stop_flag, 2));
    }

    #[test]
    fn search_json_streams_nested_values() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = br#"{"items":[{"value":"needle"},{"value":"other"}]}"#;

        let result = search_json_file(json, "needle", &ac, false, stop_flag, 10, 20_000);

        assert_eq!(result.len(), 1);
        assert!(result[0].1.contains("/items/0/value\tneedle"));
    }
}
