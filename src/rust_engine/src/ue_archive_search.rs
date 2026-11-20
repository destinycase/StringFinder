//! Unreal Engine 현지화 `.archive` 문서의 원문·번역문·Key 값만 검색합니다.
//!
//! 문서 전체의 JSON 문법·중복 키·깊이는 JSON 값만 검색과 같은 파서로 먼저 검증합니다.
//! 그다음 구조를 해석하므로, 앞부분에서 찾은 결과가 있어도 문서가 잘못되면 결과를 확정하지 않습니다.
//! 검증을 마친 문서는 줄 위치를 기억하는 작은 해석기로 한 번 더 읽으며 구조를 판정하고 검색합니다(7.4.4).
//! 이때 문서 전체를 트리로 만들지 않고, 이름공간 뼈대와 일치한 항목만 남긴 뒤 기존과 같은 방문 순서로
//! 형식 오류와 결과를 확정합니다. 그래서 메모리 사용이 파일 크기에 비례하지 않습니다.

use std::borrow::Cow;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use crate::types::{RawMatch, ResultKind};

/// 결과 내용 앞에 붙는 표식입니다. 뒤에는 `[Namespace, Key, 원문, 번역문]` JSON 배열이 옵니다.
/// 각 값에 탭·줄바꿈이 있어도 필드가 섞이지 않도록 구분자 대신 JSON 배열을 사용합니다.
pub const ARCHIVE_FIELDS_PREFIX: &str = "ARCHIVE_FIELDS|";

pub enum ArchiveSearchError {
    /// JSON 문법·중복 키 등 문서 자체의 오류입니다.
    Json(String),
    /// JSON으로는 올바르지만 UE 현지화 archive 구조가 아닌 경우입니다. 값은 문제 위치 경로입니다.
    Format(String),
}

#[cfg(test)]
struct ArchiveSearch<'a> {
    matcher: &'a crate::textkey::Matcher,
    is_exact: bool,
    max_per_file: usize,
    stop_flag: Arc<AtomicBool>,
    results: Vec<RawMatch>,
}

#[cfg(test)]
impl ArchiveSearch<'_> {
    fn value_matches(&self, value: &str) -> bool {
        // 모든 형식이 같은 비교 기준(textkey)을 씁니다.
        if self.is_exact {
            self.matcher.equals_trimmed(value)
        } else {
            self.matcher.contains(value)
        }
    }

    fn record(
        &mut self,
        key_line: usize,
        namespace: &str,
        key: &str,
        source: &str,
        translation: &str,
    ) {
        // 상한보다 하나 더 모아 두면 호출자가 잘림 표식을 붙일 수 있습니다.
        if self.results.len() > self.max_per_file {
            return;
        }
        if !(self.value_matches(key)
            || self.value_matches(source)
            || self.value_matches(translation))
        {
            return;
        }
        let fields = [namespace, key, source, translation];
        let content = match serde_json::to_string(&fields) {
            Ok(encoded) => format!("{ARCHIVE_FIELDS_PREFIX}{encoded}"),
            Err(_) => return,
        };
        // 줄 번호는 항목의 `Key` 줄입니다. 바이트 위치는 인코딩 변환 뒤라 원본과 다를 수 있어 넘기지 않습니다.
        self.results
            .push((key_line, content, None, None, ResultKind::Match));
    }
}

fn format_error(path: &str) -> ArchiveSearchError {
    ArchiveSearchError::Format(if path.is_empty() {
        "/".to_owned()
    } else {
        path.to_owned()
    })
}

/// 검증을 마친 JSON의 구조와 문자열만 담습니다. 객체 멤버는 이름이 시작한 줄 번호를 함께 기억합니다.
#[cfg(test)]
enum Node<'a> {
    Object(Vec<(Cow<'a, str>, usize, Node<'a>)>),
    Array(Vec<Node<'a>>),
    Str(Cow<'a, str>),
    Other,
}

#[cfg(test)]
impl<'a> Node<'a> {
    fn member(&self, name: &str) -> Option<(&Node<'a>, usize)> {
        match self {
            Node::Object(members) => members
                .iter()
                .find(|(key, _, _)| key == name)
                .map(|(_, line, value)| (value, *line)),
            _ => None,
        }
    }

    fn as_str(&self) -> Option<&str> {
        match self {
            Node::Str(value) => Some(value),
            _ => None,
        }
    }

    fn is_object(&self) -> bool {
        matches!(self, Node::Object(_))
    }
}

/// 기본 재귀 한도와 같은 128단계까지만 읽어 스택 고갈을 막습니다. UE archive 구조는 이보다 훨씬 얕습니다.
const MAX_NESTING: usize = 128;

struct PositionedParser<'a> {
    text: &'a str,
    bytes: &'a [u8],
    pos: usize,
    line: usize,
}

impl<'a> PositionedParser<'a> {
    fn new(text: &'a str) -> Self {
        Self {
            text,
            bytes: text.as_bytes(),
            pos: 0,
            line: 1,
        }
    }

    fn unexpected(&self) -> ArchiveSearchError {
        ArchiveSearchError::Json(format!("unexpected input at line {}", self.line))
    }

    fn skip_whitespace(&mut self) {
        while let Some(&byte) = self.bytes.get(self.pos) {
            match byte {
                // 줄바꿈은 LF, CRLF, CR입니다. CRLF는 LF에서 한 번만 셉니다.
                b'\n' => self.line += 1,
                b'\r' => {
                    if self.bytes.get(self.pos + 1) != Some(&b'\n') {
                        self.line += 1;
                    }
                }
                b' ' | b'\t' => {}
                _ => return,
            }
            self.pos += 1;
        }
    }

    fn expect(&mut self, byte: u8) -> Result<(), ArchiveSearchError> {
        self.skip_whitespace();
        if self.bytes.get(self.pos) == Some(&byte) {
            self.pos += 1;
            Ok(())
        } else {
            Err(self.unexpected())
        }
    }

    fn string(&mut self) -> Result<Cow<'a, str>, ArchiveSearchError> {
        let start = self.pos;
        self.pos += 1;
        let mut escaped = false;
        while let Some(&byte) = self.bytes.get(self.pos) {
            match byte {
                b'\\' => {
                    escaped = true;
                    self.pos += 2;
                }
                b'"' => {
                    self.pos += 1;
                    let raw = &self.text[start..self.pos];
                    return if escaped {
                        // 이스케이프 해석은 검증에 쓴 serde_json과 같은 규칙을 따릅니다.
                        serde_json::from_str::<String>(raw)
                            .map(Cow::Owned)
                            .map_err(|error| ArchiveSearchError::Json(error.to_string()))
                    } else {
                        Ok(Cow::Borrowed(&raw[1..raw.len() - 1]))
                    };
                }
                _ => self.pos += 1,
            }
        }
        Err(self.unexpected())
    }

    #[cfg(test)]
    fn value(&mut self, depth: usize) -> Result<Node<'a>, ArchiveSearchError> {
        if depth > MAX_NESTING {
            return Err(ArchiveSearchError::Format(format!("depth>{MAX_NESTING}")));
        }
        self.skip_whitespace();
        match self.bytes.get(self.pos) {
            Some(b'{') => {
                self.pos += 1;
                let mut members = Vec::new();
                self.skip_whitespace();
                if self.bytes.get(self.pos) == Some(&b'}') {
                    self.pos += 1;
                    return Ok(Node::Object(members));
                }
                loop {
                    self.skip_whitespace();
                    if self.bytes.get(self.pos) != Some(&b'"') {
                        return Err(self.unexpected());
                    }
                    let line = self.line;
                    let key = self.string()?;
                    self.expect(b':')?;
                    let value = self.value(depth + 1)?;
                    members.push((key, line, value));
                    self.skip_whitespace();
                    match self.bytes.get(self.pos) {
                        Some(b',') => self.pos += 1,
                        Some(b'}') => {
                            self.pos += 1;
                            return Ok(Node::Object(members));
                        }
                        _ => return Err(self.unexpected()),
                    }
                }
            }
            Some(b'[') => {
                self.pos += 1;
                let mut items = Vec::new();
                self.skip_whitespace();
                if self.bytes.get(self.pos) == Some(&b']') {
                    self.pos += 1;
                    return Ok(Node::Array(items));
                }
                loop {
                    items.push(self.value(depth + 1)?);
                    self.skip_whitespace();
                    match self.bytes.get(self.pos) {
                        Some(b',') => self.pos += 1,
                        Some(b']') => {
                            self.pos += 1;
                            return Ok(Node::Array(items));
                        }
                        _ => return Err(self.unexpected()),
                    }
                }
            }
            Some(b'"') => self.string().map(Node::Str),
            Some(_) => {
                // 숫자·true·false·null은 검색 대상이 아니므로 끝까지 건너뜁니다.
                while let Some(&byte) = self.bytes.get(self.pos) {
                    if matches!(byte, b',' | b'}' | b']' | b' ' | b'\t' | b'\r' | b'\n') {
                        break;
                    }
                    self.pos += 1;
                }
                Ok(Node::Other)
            }
            None => Err(self.unexpected()),
        }
    }
}

#[cfg(test)]
fn text_field<'n>(
    entry: &'n Node<'_>,
    field: &str,
    path: &str,
) -> Result<&'n str, ArchiveSearchError> {
    let text_path = format!("{path}/{field}/Text");
    entry
        .member(field)
        .filter(|(holder, _)| holder.is_object())
        .and_then(|(holder, _)| holder.member("Text"))
        .and_then(|(text, _)| text.as_str())
        .ok_or_else(|| format_error(&text_path))
}

fn join_namespace(parent: &str, own: &str) -> String {
    match (parent.is_empty(), own.is_empty()) {
        (_, true) => parent.to_owned(),
        (true, false) => own.to_owned(),
        (false, false) => format!("{parent}.{own}"),
    }
}

/// 노드의 `Children`을 먼저, 그다음 `Subnamespaces`를 문서 순서대로 방문합니다.
/// Python 경로도 같은 순서를 사용하므로 두 경로의 결과 순서가 같습니다.
#[cfg(test)]
fn walk(root: &Node<'_>, search: &mut ArchiveSearch<'_>) -> Result<(), ArchiveSearchError> {
    if !root.is_object() {
        return Err(format_error(""));
    }
    if root.member("Children").is_none() && root.member("Subnamespaces").is_none() {
        return Err(format_error("/Children"));
    }

    // (노드, 상위 Namespace, 노드 경로). 깊은 중첩에서도 재귀 호출을 쓰지 않습니다.
    let mut stack: Vec<(&Node<'_>, String, String)> = vec![(root, String::new(), String::new())];
    while let Some((node, parent_namespace, path)) = stack.pop() {
        if search.stop_flag.load(Ordering::Relaxed) {
            return Ok(());
        }
        if !node.is_object() {
            return Err(format_error(&path));
        }
        let own_namespace = match node.member("Namespace") {
            None => "",
            Some((value, _)) => value
                .as_str()
                .ok_or_else(|| format_error(&format!("{path}/Namespace")))?,
        };
        let namespace = join_namespace(&parent_namespace, own_namespace);

        if let Some((children, _)) = node.member("Children") {
            let Node::Array(children) = children else {
                return Err(format_error(&format!("{path}/Children")));
            };
            for (index, entry) in children.iter().enumerate() {
                let child_path = format!("{path}/Children/{index}");
                if !entry.is_object() {
                    return Err(format_error(&child_path));
                }
                let (key, key_line) = entry
                    .member("Key")
                    .and_then(|(value, line)| value.as_str().map(|key| (key, line)))
                    .ok_or_else(|| format_error(&format!("{child_path}/Key")))?;
                let source = text_field(entry, "Source", &child_path)?;
                let translation = text_field(entry, "Translation", &child_path)?;
                search.record(key_line, &namespace, key, source, translation);
            }
        }

        if let Some((subnamespaces, _)) = node.member("Subnamespaces") {
            let Node::Array(subnamespaces) = subnamespaces else {
                return Err(format_error(&format!("{path}/Subnamespaces")));
            };
            // 스택은 뒤에서 꺼내므로 역순으로 넣어 문서 순서를 유지합니다.
            for (index, child) in subnamespaces.iter().enumerate().rev() {
                stack.push((
                    child,
                    namespace.clone(),
                    format!("{path}/Subnamespaces/{index}"),
                ));
            }
        }
    }
    Ok(())
}

/// 항목(Children의 원소) 하나를 읽은 결과입니다. 값이 없거나 형식이 틀리면 `None`입니다.
struct EntryFields<'a> {
    key: Option<(Cow<'a, str>, usize)>,
    source: Option<Cow<'a, str>>,
    translation: Option<Cow<'a, str>>,
}

/// 일치한 항목입니다: (Key 줄, Key, 원문, 번역문).
type ArchiveMatch<'a> = (usize, Cow<'a, str>, Cow<'a, str>, Cow<'a, str>);

enum ChildrenInfo<'a> {
    NotArray,
    /// 문서 순서로 처음 나온 형식 오류(원소 번호, 경로 꼬리)와, 일치한 항목입니다.
    Array {
        error: Option<(usize, &'static str)>,
        matches: Vec<ArchiveMatch<'a>>,
    },
}

enum SubnamespacesInfo<'a> {
    NotArray,
    /// 원소마다 이름공간 노드입니다. 객체가 아닌 원소는 `None`입니다.
    Array(Vec<Option<NamespaceNode<'a>>>),
}

/// 이름공간 객체 하나에서 검색에 필요한 것만 남긴 뼈대입니다. 항목 본문은 일치한 것만 남습니다.
struct NamespaceNode<'a> {
    /// `None`: Namespace 멤버 없음, `Some(None)`: 문자열이 아님.
    namespace: Option<Option<Cow<'a, str>>>,
    children: Option<ChildrenInfo<'a>>,
    subnamespaces: Option<SubnamespacesInfo<'a>>,
}

/// 문서를 한 번 읽으며 뼈대를 만듭니다. 문서 전체 트리를 메모리에 쌓지 않습니다(7.4.4).
struct StreamingReader<'p, 'a, 'm> {
    parser: &'p mut PositionedParser<'a>,
    matcher: &'m crate::textkey::Matcher,
    is_exact: bool,
    /// 이름공간 하나에 남길 일치 항목 수입니다(상한 + 1, 잘림 표식용).
    keep_per_node: usize,
    stop_flag: &'m AtomicBool,
}

impl<'a> StreamingReader<'_, 'a, '_> {
    fn check_depth(depth: usize) -> Result<(), ArchiveSearchError> {
        if depth > MAX_NESTING {
            return Err(ArchiveSearchError::Format(format!("depth>{MAX_NESTING}")));
        }
        Ok(())
    }

    fn value_matches(&self, value: &str) -> bool {
        if self.is_exact {
            self.matcher.equals_trimmed(value)
        } else {
            self.matcher.contains(value)
        }
    }

    fn peek(&mut self) -> Option<u8> {
        self.parser.skip_whitespace();
        self.parser.bytes.get(self.parser.pos).copied()
    }

    /// 객체 멤버를 차례로 돌며 `on_member(이름, 이름 줄, 깊이)`를 부릅니다. 현재 위치는 `{`입니다.
    fn object_members(
        &mut self,
        depth: usize,
        mut on_member: impl FnMut(
            &mut Self,
            Cow<'a, str>,
            usize,
            usize,
        ) -> Result<(), ArchiveSearchError>,
    ) -> Result<(), ArchiveSearchError> {
        Self::check_depth(depth)?;
        self.parser.pos += 1;
        if self.peek() == Some(b'}') {
            self.parser.pos += 1;
            return Ok(());
        }
        loop {
            if self.peek() != Some(b'"') {
                return Err(self.parser.unexpected());
            }
            let line = self.parser.line;
            let name = self.parser.string()?;
            self.parser.expect(b':')?;
            on_member(self, name, line, depth + 1)?;
            match self.peek() {
                Some(b',') => self.parser.pos += 1,
                Some(b'}') => {
                    self.parser.pos += 1;
                    return Ok(());
                }
                _ => return Err(self.parser.unexpected()),
            }
        }
    }

    /// 배열 원소를 차례로 돌며 `on_item(번호, 깊이)`를 부릅니다. 현재 위치는 `[`입니다.
    fn array_items(
        &mut self,
        depth: usize,
        mut on_item: impl FnMut(&mut Self, usize, usize) -> Result<(), ArchiveSearchError>,
    ) -> Result<(), ArchiveSearchError> {
        Self::check_depth(depth)?;
        self.parser.pos += 1;
        if self.peek() == Some(b']') {
            self.parser.pos += 1;
            return Ok(());
        }
        let mut index = 0usize;
        loop {
            on_item(self, index, depth + 1)?;
            index += 1;
            match self.peek() {
                Some(b',') => self.parser.pos += 1,
                Some(b']') => {
                    self.parser.pos += 1;
                    return Ok(());
                }
                _ => return Err(self.parser.unexpected()),
            }
        }
    }

    /// 값을 읽고 버립니다. 문자열은 해석하지 않고 건너뜁니다.
    fn skip_value(&mut self, depth: usize) -> Result<(), ArchiveSearchError> {
        Self::check_depth(depth)?;
        match self.peek() {
            Some(b'{') => {
                self.object_members(depth, |reader, _, _, inner| reader.skip_value(inner))
            }
            Some(b'[') => self.array_items(depth, |reader, _, inner| reader.skip_value(inner)),
            Some(b'"') => self.skip_string(),
            Some(_) => {
                while let Some(&byte) = self.parser.bytes.get(self.parser.pos) {
                    if matches!(byte, b',' | b'}' | b']' | b' ' | b'\t' | b'\r' | b'\n') {
                        break;
                    }
                    self.parser.pos += 1;
                }
                Ok(())
            }
            None => Err(self.parser.unexpected()),
        }
    }

    fn skip_string(&mut self) -> Result<(), ArchiveSearchError> {
        self.parser.pos += 1;
        while let Some(&byte) = self.parser.bytes.get(self.parser.pos) {
            match byte {
                b'\\' => self.parser.pos += 2,
                b'"' => {
                    self.parser.pos += 1;
                    return Ok(());
                }
                _ => self.parser.pos += 1,
            }
        }
        Err(self.parser.unexpected())
    }

    /// 문자열이면 읽고, 아니면 건너뛰고 `None`을 돌려줍니다.
    fn string_or_skip(&mut self, depth: usize) -> Result<Option<Cow<'a, str>>, ArchiveSearchError> {
        Self::check_depth(depth)?;
        if self.peek() == Some(b'"') {
            self.parser.string().map(Some)
        } else {
            self.skip_value(depth).map(|_| None)
        }
    }

    /// `Source`·`Translation`의 `Text` 문자열입니다. 객체가 아니거나 Text가 문자열이 아니면 `None`입니다.
    fn text_holder(&mut self, depth: usize) -> Result<Option<Cow<'a, str>>, ArchiveSearchError> {
        Self::check_depth(depth)?;
        if self.peek() != Some(b'{') {
            return self.skip_value(depth).map(|_| None);
        }
        let mut text = None;
        self.object_members(depth, |reader, name, _, inner| {
            if name == "Text" {
                text = reader.string_or_skip(inner)?;
                Ok(())
            } else {
                reader.skip_value(inner)
            }
        })?;
        Ok(text)
    }

    /// 항목 하나입니다. 객체가 아니면 `None`입니다.
    fn entry(&mut self, depth: usize) -> Result<Option<EntryFields<'a>>, ArchiveSearchError> {
        Self::check_depth(depth)?;
        if self.peek() != Some(b'{') {
            return self.skip_value(depth).map(|_| None);
        }
        let mut fields = EntryFields {
            key: None,
            source: None,
            translation: None,
        };
        self.object_members(depth, |reader, name, line, inner| {
            match name.as_ref() {
                "Key" => fields.key = reader.string_or_skip(inner)?.map(|key| (key, line)),
                "Source" => fields.source = reader.text_holder(inner)?,
                "Translation" => fields.translation = reader.text_holder(inner)?,
                _ => reader.skip_value(inner)?,
            }
            Ok(())
        })?;
        Ok(Some(fields))
    }

    fn children(&mut self, depth: usize) -> Result<ChildrenInfo<'a>, ArchiveSearchError> {
        Self::check_depth(depth)?;
        if self.peek() != Some(b'[') {
            return self.skip_value(depth).map(|_| ChildrenInfo::NotArray);
        }
        let mut error: Option<(usize, &'static str)> = None;
        let mut matches: Vec<ArchiveMatch<'a>> = Vec::new();
        self.array_items(depth, |reader, index, inner| {
            if reader.stop_flag.load(Ordering::Relaxed) {
                // 멈춤은 바로 읽기를 끝냅니다. 호출한 쪽이 멈춤 표시를 보고 빈 결과로 바꿉니다.
                return Err(ArchiveSearchError::Format(String::new()));
            }
            let entry = reader.entry(inner)?;
            if error.is_some() {
                return Ok(());
            }
            // 기존 순회와 같은 순서로 검사합니다: 객체 → Key → Source/Text → Translation/Text.
            let Some(fields) = entry else {
                error = Some((index, ""));
                return Ok(());
            };
            let Some((key, key_line)) = fields.key else {
                error = Some((index, "/Key"));
                return Ok(());
            };
            let Some(source) = fields.source else {
                error = Some((index, "/Source/Text"));
                return Ok(());
            };
            let Some(translation) = fields.translation else {
                error = Some((index, "/Translation/Text"));
                return Ok(());
            };
            if matches.len() < reader.keep_per_node
                && (reader.value_matches(&key)
                    || reader.value_matches(&source)
                    || reader.value_matches(&translation))
            {
                matches.push((key_line, key, source, translation));
            }
            Ok(())
        })?;
        Ok(ChildrenInfo::Array { error, matches })
    }

    fn subnamespaces(&mut self, depth: usize) -> Result<SubnamespacesInfo<'a>, ArchiveSearchError> {
        Self::check_depth(depth)?;
        if self.peek() != Some(b'[') {
            return self.skip_value(depth).map(|_| SubnamespacesInfo::NotArray);
        }
        let mut nodes = Vec::new();
        self.array_items(depth, |reader, _, inner| {
            nodes.push(reader.namespace_node(inner)?);
            Ok(())
        })?;
        Ok(SubnamespacesInfo::Array(nodes))
    }

    /// 이름공간 노드입니다. 객체가 아니면 `None`입니다.
    fn namespace_node(
        &mut self,
        depth: usize,
    ) -> Result<Option<NamespaceNode<'a>>, ArchiveSearchError> {
        Self::check_depth(depth)?;
        if self.peek() != Some(b'{') {
            return self.skip_value(depth).map(|_| None);
        }
        let mut node = NamespaceNode {
            namespace: None,
            children: None,
            subnamespaces: None,
        };
        self.object_members(depth, |reader, name, _, inner| {
            match name.as_ref() {
                "Namespace" => node.namespace = Some(reader.string_or_skip(inner)?),
                "Children" => node.children = Some(reader.children(inner)?),
                "Subnamespaces" => node.subnamespaces = Some(reader.subnamespaces(inner)?),
                _ => reader.skip_value(inner)?,
            }
            Ok(())
        })?;
        Ok(Some(node))
    }
}

/// 뼈대를 기존 순회와 같은 순서(노드의 Children 먼저, 그다음 Subnamespaces를 문서 순서대로)로 돌며
/// 형식 오류를 찾고 결과를 확정합니다.
fn finish(
    root: Option<&NamespaceNode<'_>>,
    max_per_file: usize,
) -> Result<Vec<RawMatch>, ArchiveSearchError> {
    let Some(root) = root else {
        return Err(format_error(""));
    };
    if root.children.is_none() && root.subnamespaces.is_none() {
        return Err(format_error("/Children"));
    }
    let mut results: Vec<RawMatch> = Vec::new();
    let mut stack: Vec<(Option<&NamespaceNode<'_>>, String, String)> =
        vec![(Some(root), String::new(), String::new())];
    while let Some((node, parent_namespace, path)) = stack.pop() {
        let Some(node) = node else {
            return Err(format_error(&path));
        };
        let own_namespace = match &node.namespace {
            None => "",
            Some(Some(value)) => value.as_ref(),
            Some(None) => return Err(format_error(&format!("{path}/Namespace"))),
        };
        let namespace = join_namespace(&parent_namespace, own_namespace);

        match &node.children {
            None => {}
            Some(ChildrenInfo::NotArray) => return Err(format_error(&format!("{path}/Children"))),
            Some(ChildrenInfo::Array { error, matches }) => {
                if let Some((index, tail)) = error {
                    return Err(format_error(&format!("{path}/Children/{index}{tail}")));
                }
                for (key_line, key, source, translation) in matches {
                    // 상한보다 하나 더 모아 두면 호출자가 잘림 표식을 붙일 수 있습니다.
                    if results.len() > max_per_file {
                        break;
                    }
                    let fields = [namespace.as_str(), key, source, translation];
                    if let Ok(encoded) = serde_json::to_string(&fields) {
                        // 바이트 위치는 인코딩 변환 뒤라 원본과 다를 수 있어 넘기지 않습니다.
                        results.push((
                            *key_line,
                            format!("{ARCHIVE_FIELDS_PREFIX}{encoded}"),
                            None,
                            None,
                            ResultKind::Match,
                        ));
                    }
                }
            }
        }

        match &node.subnamespaces {
            None => {}
            Some(SubnamespacesInfo::NotArray) => {
                return Err(format_error(&format!("{path}/Subnamespaces")))
            }
            Some(SubnamespacesInfo::Array(nodes)) => {
                // 스택은 뒤에서 꺼내므로 역순으로 넣어 문서 순서를 유지합니다.
                for (index, child) in nodes.iter().enumerate().rev() {
                    stack.push((
                        child.as_ref(),
                        namespace.clone(),
                        format!("{path}/Subnamespaces/{index}"),
                    ));
                }
            }
        }
    }
    Ok(results)
}

#[allow(clippy::too_many_arguments)]
pub fn search_archive_file(
    mmap: &[u8],
    matcher: &crate::textkey::Matcher,
    is_exact: bool,
    stop_flag: Arc<AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
) -> Result<Vec<RawMatch>, ArchiveSearchError> {
    // archive의 구조 필드는 하나씩만 있어야 하므로 JSON 중복 키 설정과 관계없이 중복을 허용하지 않습니다.
    let depth_limit_reached =
        crate::json_search::validate_json_document(mmap, stop_flag.clone(), max_json_depth)
            .map_err(ArchiveSearchError::Json)?;
    if stop_flag.load(Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    if depth_limit_reached {
        return Err(ArchiveSearchError::Format(format!(
            "depth>{max_json_depth}"
        )));
    }

    let body = mmap.strip_prefix(b"\xef\xbb\xbf").unwrap_or(mmap);
    let text =
        std::str::from_utf8(body).map_err(|error| ArchiveSearchError::Json(error.to_string()))?;
    let mut parser = PositionedParser::new(text);
    let mut reader = StreamingReader {
        parser: &mut parser,
        matcher,
        is_exact,
        keep_per_node: max_per_file.saturating_add(1),
        stop_flag: &stop_flag,
    };
    let root = reader.namespace_node(0);
    if stop_flag.load(Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    finish(root?.as_ref(), max_per_file)
}

/// 7.4.3까지 쓰던 방식(문서 전체를 트리로 만든 뒤 순회)입니다. 새 방식과 결과를 대조하는 시험에만 씁니다.
#[cfg(test)]
fn search_archive_file_tree(
    mmap: &[u8],
    matcher: &crate::textkey::Matcher,
    is_exact: bool,
    stop_flag: Arc<AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
) -> Result<Vec<RawMatch>, ArchiveSearchError> {
    let depth_limit_reached =
        crate::json_search::validate_json_document(mmap, stop_flag.clone(), max_json_depth)
            .map_err(ArchiveSearchError::Json)?;
    if stop_flag.load(Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    if depth_limit_reached {
        return Err(ArchiveSearchError::Format(format!(
            "depth>{max_json_depth}"
        )));
    }

    let body = mmap.strip_prefix(b"\xef\xbb\xbf").unwrap_or(mmap);
    let text =
        std::str::from_utf8(body).map_err(|error| ArchiveSearchError::Json(error.to_string()))?;
    let document = PositionedParser::new(text).value(0)?;

    let mut search = ArchiveSearch {
        matcher,
        is_exact,
        max_per_file,
        stop_flag,
        results: Vec::new(),
    };
    walk(&document, &mut search)?;
    if search.stop_flag.load(Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    Ok(search.results)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(document: &str, pattern: &str, exact: bool) -> Result<Vec<Vec<String>>, String> {
        let matcher = crate::textkey::Matcher::new(pattern).unwrap();
        search_archive_file(
            document.as_bytes(),
            &matcher,
            exact,
            Arc::new(AtomicBool::new(false)),
            100,
            20_000,
        )
        .map(|results| {
            results
                .into_iter()
                .map(|(_, content, _, _, _)| {
                    serde_json::from_str(content.strip_prefix(ARCHIVE_FIELDS_PREFIX).unwrap())
                        .unwrap()
                })
                .collect()
        })
        .map_err(|error| match error {
            ArchiveSearchError::Json(detail) => format!("json:{detail}"),
            ArchiveSearchError::Format(detail) => format!("format:{detail}"),
        })
    }

    const SAMPLE: &str = r#"{"Subnamespaces":[
        {"Namespace":"System_UI","Children":[
            {"Key":"ID_BTN_CONFIRM","Source":{"Text":"Confirm and Proceed"},"Translation":{"Text":"확인 후 진행"}},
            {"Key":"ID_LBL_WELCOME","Source":{"Text":"Welcome to StringFinder"},"Translation":{"Text":"StringFinder에 오신 것을 환영합니다"}}]},
        {"Namespace":"Game_Dialog","Children":[
            {"Key":"NPC_GREET_01","Source":{"Text":"Hello, brave traveler!"},"Translation":{"Text":"안녕, 용감한 여행자여!"}}]}]}"#;

    #[test]
    fn searches_source_translation_and_key_but_not_namespace() {
        assert_eq!(
            run(SAMPLE, "proceed", false).unwrap()[0][1],
            "ID_BTN_CONFIRM"
        );
        assert_eq!(run(SAMPLE, "여행자", false).unwrap()[0][0], "Game_Dialog");
        assert_eq!(run(SAMPLE, "npc_greet", false).unwrap().len(), 1);
        assert!(run(SAMPLE, "System_UI", false).unwrap().is_empty());
    }

    #[test]
    fn one_entry_is_one_result_even_when_several_values_match() {
        let results = run(SAMPLE, "StringFinder", false).unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(
            results[0],
            [
                "System_UI",
                "ID_LBL_WELCOME",
                "Welcome to StringFinder",
                "StringFinder에 오신 것을 환영합니다"
            ]
        );
    }

    #[test]
    fn exact_match_compares_each_whole_value() {
        assert_eq!(run(SAMPLE, "id_btn_confirm", true).unwrap().len(), 1);
        assert!(run(SAMPLE, "ID_BTN", true).unwrap().is_empty());
        assert_eq!(run(SAMPLE, "확인 후 진행", true).unwrap().len(), 1);
    }

    #[test]
    fn root_children_and_nested_namespaces_are_visited_in_document_order() {
        let document = r#"{"Namespace":"","Children":[
            {"Key":"ROOT","Source":{"Text":"needle root"},"Translation":{"Text":""}}],
            "Subnamespaces":[{"Namespace":"UI","Subnamespaces":[{"Namespace":"Menu","Children":[
            {"Key":"DEEP","Source":{"Text":"needle deep"},"Translation":{"Text":"x"}}]}]}]}"#;
        let results = run(document, "needle", false).unwrap();
        assert_eq!(results[0][..2], ["", "ROOT"]);
        assert_eq!(results[1][..2], ["UI.Menu", "DEEP"]);
    }

    #[test]
    fn structure_violations_report_the_offending_path() {
        assert_eq!(run("[]", "x", false).unwrap_err(), "format:/");
        assert_eq!(
            run(r#"{"FormatVersion":2}"#, "x", false).unwrap_err(),
            "format:/Children"
        );
        assert_eq!(
            run(r#"{"Subnamespaces":[{"Children":[{"Source":{"Text":"a"},"Translation":{"Text":"b"}}]}]}"#, "a", false)
                .unwrap_err(),
            "format:/Subnamespaces/0/Children/0/Key"
        );
        assert_eq!(
            run(
                r#"{"Children":[{"Key":"k","Source":{"Text":"a"}}]}"#,
                "a",
                false
            )
            .unwrap_err(),
            "format:/Children/0/Translation/Text"
        );
    }

    #[test]
    fn results_carry_the_line_of_each_key_member() {
        let document = "{\n  \"Subnamespaces\": [\n    {\n      \"Namespace\": \"NS\",\n      \"Children\": [\n        {\n          \"Source\": {\"Text\": \"needle \\u00e9\"},\n          \"Translation\": {\"Text\": \"\"},\n          \"Key\": \"K1\"\n        },\n        {\"Key\": \"K2\", \"Source\": {\"Text\": \"needle\"}, \"Translation\": {\"Text\": \"\"}}\n      ]\n    }\n  ]\n}";
        let matcher = crate::textkey::Matcher::new("needle").unwrap();
        let results = search_archive_file(
            document.as_bytes(),
            &matcher,
            false,
            Arc::new(AtomicBool::new(false)),
            100,
            20_000,
        )
        .ok()
        .unwrap();
        let lines: Vec<usize> = results.iter().map(|result| result.0).collect();
        assert_eq!(lines, [9, 11]);
        assert!(results[0].1.contains("needle é"));
    }

    #[test]
    fn json_errors_and_duplicate_keys_are_document_errors() {
        assert!(run(r#"{"Children":[}"#, "x", false)
            .unwrap_err()
            .starts_with("json:"));
        assert!(run(r#"{"Children":[],"Children":[]}"#, "x", false)
            .unwrap_err()
            .starts_with("json:"));
    }

    #[test]
    fn collects_one_result_past_the_limit_for_truncation() {
        let children: Vec<String> = (0..5)
            .map(|index| {
                format!(r#"{{"Key":"K{index}","Source":{{"Text":"needle"}},"Translation":{{"Text":""}}}}"#)
            })
            .collect();
        let document = format!(r#"{{"Children":[{}]}}"#, children.join(","));
        let matcher = crate::textkey::Matcher::new("needle").unwrap();
        let results = search_archive_file(
            document.as_bytes(),
            &matcher,
            false,
            Arc::new(AtomicBool::new(false)),
            2,
            20_000,
        )
        .ok()
        .unwrap();
        assert_eq!(results.len(), 3);
    }
}

#[cfg(test)]
mod streaming_equivalence_tests {
    use super::*;

    /// 결과·오류를 비교할 수 있는 모양으로 바꿉니다.
    fn outcome(
        result: Result<Vec<RawMatch>, ArchiveSearchError>,
    ) -> Result<Vec<(usize, String)>, String> {
        result
            .map(|items| {
                items
                    .into_iter()
                    .map(|(line, content, _, _, _)| (line, content))
                    .collect()
            })
            .map_err(|error| match error {
                ArchiveSearchError::Json(detail) => format!("json:{detail}"),
                ArchiveSearchError::Format(detail) => format!("format:{detail}"),
            })
    }

    fn both(document: &str, pattern: &str, exact: bool, cap: usize) -> bool {
        let matcher = crate::textkey::Matcher::new(pattern).unwrap();
        let run = |tree: bool| {
            let stop = Arc::new(AtomicBool::new(false));
            let result = if tree {
                search_archive_file_tree(document.as_bytes(), &matcher, exact, stop, cap, 20_000)
            } else {
                search_archive_file(document.as_bytes(), &matcher, exact, stop, cap, 20_000)
            };
            outcome(result)
        };
        let streaming = run(false);
        assert_eq!(
            streaming,
            run(true),
            "pattern {pattern:?} exact {exact} cap {cap}\n{document}"
        );
        matches!(streaming, Ok(ref items) if !items.is_empty())
    }

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 << 13;
            self.0 ^= self.0 >> 7;
            self.0 ^= self.0 << 17;
            self.0
        }
        fn pick(&mut self, n: usize) -> usize {
            (self.next() % n as u64) as usize
        }
        fn chance(&mut self, percent: u64) -> bool {
            self.next() % 100 < percent
        }
    }

    const TEXTS: [&str; 10] = [
        "\"needle\"",
        "\"Needle 값\"",
        "\"목표단어\"",
        "\"plain\"",
        "\"\"",
        "\"esc \\\"needle\\\" \\\\ \\u00e9\"",
        "\"line\\nneedle\"",
        "\" 목표단어 \"",
        "\"NEEDLE\"",
        "\"\\ud55c\\uae00 needle\"",
    ];
    const BREAKS: [&str; 5] = ["", " ", "\n", "\r\n", "\r"];
    const ODD: [&str; 6] = ["1", "null", "true", "[]", "{}", "[\"needle\"]"];

    fn gap(rng: &mut Rng) -> &'static str {
        BREAKS[rng.pick(BREAKS.len())]
    }

    fn text_holder(rng: &mut Rng) -> String {
        if rng.chance(4) {
            return ODD[rng.pick(ODD.len())].to_owned();
        }
        let mut members = Vec::new();
        if !rng.chance(4) {
            let value = if rng.chance(4) {
                ODD[rng.pick(ODD.len())].to_owned()
            } else {
                TEXTS[rng.pick(TEXTS.len())].to_owned()
            };
            members.push(format!("\"Text\":{}{value}", gap(rng)));
        }
        if rng.chance(30) {
            members.push(format!(
                "\"Extra\":{{\"Text\":{}}}",
                TEXTS[rng.pick(TEXTS.len())]
            ));
        }
        if rng.chance(50) {
            members.reverse();
        }
        format!(
            "{{{}{}}}",
            gap(rng),
            members.join(&format!(",{}", gap(rng)))
        )
    }

    fn entry(rng: &mut Rng) -> String {
        if rng.chance(3) {
            return ODD[rng.pick(ODD.len())].to_owned();
        }
        let mut members = Vec::new();
        if !rng.chance(3) {
            let key = if rng.chance(3) {
                ODD[rng.pick(ODD.len())].to_owned()
            } else {
                TEXTS[rng.pick(TEXTS.len())].to_owned()
            };
            members.push(format!("\"Key\":{key}"));
        }
        if !rng.chance(3) {
            members.push(format!("\"Source\":{}{}", gap(rng), text_holder(rng)));
        }
        if !rng.chance(3) {
            members.push(format!("\"Translation\":{}", text_holder(rng)));
        }
        if rng.chance(30) {
            members.push(format!(
                "\"Meta\":{{\"a\":[1,{{\"b\":{}}}],\"Key\":\"x\"}}",
                TEXTS[rng.pick(TEXTS.len())]
            ));
        }
        // 멤버 순서를 섞습니다(Key가 Source 뒤에 나오는 경우 등).
        for index in (1..members.len()).rev() {
            let other = rng.pick(index + 1);
            members.swap(index, other);
        }
        format!(
            "{{{}{}{}}}",
            gap(rng),
            members.join(&format!(",{}", gap(rng))),
            gap(rng)
        )
    }

    fn namespace_node(rng: &mut Rng, depth: usize) -> String {
        if depth > 0 && rng.chance(3) {
            return ODD[rng.pick(ODD.len())].to_owned();
        }
        let mut members = Vec::new();
        if rng.chance(70) {
            let value = if rng.chance(5) {
                ODD[rng.pick(ODD.len())].to_owned()
            } else {
                ["\"UI\"", "\"\"", "\"게임\"", "\"a\\\"b\""][rng.pick(4)].to_owned()
            };
            members.push(format!("\"Namespace\":{value}"));
        }
        if rng.chance(75) {
            let value = if rng.chance(4) {
                ODD[rng.pick(3)].to_owned()
            } else {
                let count = rng.pick(5);
                let items: Vec<String> = (0..count).map(|_| entry(rng)).collect();
                format!("[{}{}]", gap(rng), items.join(&format!(",{}", gap(rng))))
            };
            members.push(format!("\"Children\":{value}"));
        }
        if depth < 3 && rng.chance(55) {
            let value = if rng.chance(4) {
                ODD[rng.pick(3)].to_owned()
            } else {
                let count = rng.pick(4);
                let items: Vec<String> =
                    (0..count).map(|_| namespace_node(rng, depth + 1)).collect();
                format!("[{}]", items.join(","))
            };
            members.push(format!("\"Subnamespaces\":{value}"));
        }
        if rng.chance(25) {
            members.push("\"FormatVersion\":2".to_owned());
        }
        for index in (1..members.len()).rev() {
            let other = rng.pick(index + 1);
            members.swap(index, other);
        }
        format!(
            "{{{}{}}}",
            gap(rng),
            members.join(&format!(",{}", gap(rng)))
        )
    }

    /// 새 방식(한 번 읽기)이 예전 방식(트리)과 결과·줄 번호·오류 경로까지 같은지 무작위 문서로 확인합니다.
    #[test]
    fn streaming_reader_matches_tree_reader_on_random_documents() {
        let mut rng = Rng(0x5DEE_CE66_D1CE_4E5B);
        let patterns = [
            ("needle", false),
            ("목표단어", false),
            ("needle", true),
            ("목표단어", true),
            ("값", false),
        ];
        let mut with_results = 0usize;
        for _ in 0..6_000 {
            let document = if rng.chance(3) {
                ODD[rng.pick(ODD.len())].to_owned()
            } else {
                namespace_node(&mut rng, 0)
            };
            let (pattern, exact) = patterns[rng.pick(patterns.len())];
            let cap = [0, 1, 2, 100][rng.pick(4)];
            if both(&document, pattern, exact, cap) {
                with_results += 1;
            }
        }
        // 오류로 끝나는 문서만 만들지 않았는지 확인합니다.
        assert!(with_results > 300, "{with_results}");
    }

    #[test]
    fn streaming_reader_matches_tree_reader_on_edge_documents() {
        let deep_open = "[".repeat(130);
        let deep_close = "]".repeat(130);
        let documents = [
            String::from("{}"),
            String::from("{\"Subnamespaces\":[]}"),
            String::from("{\"Children\":[],\"Namespace\":1}"),
            // Subnamespaces가 Children보다 앞에 나와도 Children을 먼저 방문합니다.
            String::from("{\"Subnamespaces\":[{\"Namespace\":\"B\",\"Children\":[{\"Key\":\"k2\",\"Source\":{\"Text\":\"needle\"},\"Translation\":{\"Text\":\"\"}}]}],\"Namespace\":\"A\",\"Children\":[{\"Key\":\"k1\",\"Source\":{\"Text\":\"needle\"},\"Translation\":{\"Text\":\"\"}}]}"),
            // 앞 노드의 형식 오류가 뒤 노드의 오류보다 먼저 보고됩니다.
            String::from("{\"Subnamespaces\":[{\"Children\":[{\"Key\":1}]},{\"Children\":5}]}"),
            // 일치 뒤에 형식 오류가 있으면 결과는 버려집니다.
            String::from("{\"Children\":[{\"Key\":\"k\",\"Source\":{\"Text\":\"needle\"},\"Translation\":{\"Text\":\"\"}},{\"Key\":\"k\"}]}"),
            format!("{{\"Children\":[],\"Extra\":{deep_open}{deep_close}}}"),
            String::from("{\"Children\":[{\"Key\":\"k\",\"Source\":{\"Text\":\"needle\"},\"Translation\":{\"Text\":\"\"},\"Key2\":{\"Text\":\"x\"}}]}"),
        ];
        for document in &documents {
            for cap in [0, 1, 100] {
                both(document, "needle", false, cap);
                both(document, "needle", true, cap);
            }
        }
    }

    #[test]
    fn stop_flag_returns_no_results() {
        let children: Vec<String> = (0..50)
            .map(|index| format!("{{\"Key\":\"K{index}\",\"Source\":{{\"Text\":\"needle\"}},\"Translation\":{{\"Text\":\"\"}}}}"))
            .collect();
        let document = format!("{{\"Children\":[{}]}}", children.join(","));
        let matcher = crate::textkey::Matcher::new("needle").unwrap();
        let results = search_archive_file(
            document.as_bytes(),
            &matcher,
            false,
            Arc::new(AtomicBool::new(true)),
            100,
            20_000,
        );
        assert!(matches!(results, Ok(ref items) if items.is_empty()));
    }
}
