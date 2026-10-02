//! Lazy XLSX format lookup: serial zero can be a date or a time. Do not guess.
use quick_xml::{events::Event, Reader};
use std::{
    collections::{HashMap, HashSet},
    fs::File,
    io::BufReader,
    path::Path,
};
use zip::ZipArchive;

pub type DateCells = HashSet<(String, String)>;
type Attributes = HashMap<String, String>;

fn attributes(element: &quick_xml::events::BytesStart<'_>) -> Result<Attributes, String> {
    element
        .attributes()
        .map(|attribute| {
            let attribute = attribute.map_err(|e| e.to_string())?;
            Ok((
                String::from_utf8_lossy(attribute.key.as_ref())
                    .rsplit(':')
                    .next()
                    .unwrap_or("")
                    .to_owned(),
                attribute
                    .unescape_value()
                    .map_err(|e| e.to_string())?
                    .into_owned(),
            ))
        })
        .collect()
}

fn metadata(
    archive: &mut ZipArchive<File>,
    member: &str,
) -> Result<Vec<(String, Attributes)>, String> {
    let mut reader = Reader::from_reader(BufReader::new(
        archive.by_name(member).map_err(|e| e.to_string())?,
    ));
    let mut buffer = Vec::new();
    let mut entries = Vec::new();
    let mut cell_formats = false;
    loop {
        match reader
            .read_event_into(&mut buffer)
            .map_err(|e| e.to_string())?
        {
            Event::Start(ref e) | Event::Empty(ref e) => {
                let name = String::from_utf8_lossy(e.local_name().as_ref()).into_owned();
                if name == "cellXfs" {
                    cell_formats = true;
                }
                if matches!(name.as_str(), "numFmt" | "sheet" | "Relationship")
                    || (name == "xf" && cell_formats)
                {
                    entries.push((name, attributes(e)?));
                }
            }
            Event::End(e) if e.local_name().as_ref() == b"cellXfs" => cell_formats = false,
            Event::Eof => break,
            _ => {}
        }
        buffer.clear();
    }
    Ok(entries)
}

fn is_date_format(code: &str) -> bool {
    let mut quoted = false;
    let mut bracket = false;
    let mut escape = false;
    for c in code.chars() {
        if escape {
            escape = false;
            continue;
        }
        match c {
            '\\' | '_' | '*' if !quoted => escape = true,
            '"' => quoted = !quoted,
            '[' if !quoted => bracket = true,
            ']' if !quoted => bracket = false,
            'y' | 'Y' | 'd' | 'D' if !quoted && !bracket => return true,
            _ => {}
        }
    }
    false
}

pub fn read_date_cells(path: &Path, sheet_name: &str) -> Result<DateCells, String> {
    let mut archive =
        ZipArchive::new(File::open(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let styles = metadata(&mut archive, "xl/styles.xml")?;
    let custom: HashMap<_, _> = styles
        .iter()
        .filter(|(n, _)| n == "numFmt")
        .filter_map(|(_, a)| Some((a.get("numFmtId")?.clone(), a.get("formatCode")?.clone())))
        .collect();
    let date_styles: HashSet<_> = styles
        .iter()
        .filter(|(n, _)| n == "xf")
        .enumerate()
        .filter_map(|(i, (_, a))| {
            let id = a.get("numFmtId").map(String::as_str).unwrap_or("0");
            let number = id.parse::<u32>().unwrap_or(0);
            ((14..=17).contains(&number)
                || matches!(number, 22 | 27 | 28 | 29 | 30 | 31 | 34 | 35 | 36)
                || (50..=58).contains(&number)
                || custom.get(id).is_some_and(|code| is_date_format(code)))
            .then(|| i.to_string())
        })
        .collect();
    let targets: HashMap<_, _> = metadata(&mut archive, "xl/_rels/workbook.xml.rels")?
        .into_iter()
        .filter_map(|(_, a)| Some((a.get("Id")?.clone(), a.get("Target")?.clone())))
        .collect();
    let sheets = metadata(&mut archive, "xl/workbook.xml")?;
    let mut cells = DateCells::new();
    for (_, sheet) in sheets.into_iter().filter(|(n, _)| n == "sheet") {
        let name = sheet.get("name").ok_or("Missing sheet name")?;
        if name != sheet_name {
            continue;
        }
        let target = targets
            .get(sheet.get("id").ok_or("Missing sheet relation")?)
            .ok_or("Missing sheet target")?;
        let member = if target.starts_with('/') {
            target.trim_start_matches('/').to_owned()
        } else {
            let mut parts = vec!["xl"];
            for part in target.split('/') {
                match part {
                    ".." => {
                        parts.pop();
                    }
                    "." | "" => {}
                    p => parts.push(p),
                }
            }
            parts.join("/")
        };
        let mut reader = Reader::from_reader(BufReader::new(
            archive.by_name(&member).map_err(|e| e.to_string())?,
        ));
        let mut buffer = Vec::new();
        let mut coordinate = None;
        let mut in_value = false;
        loop {
            match reader
                .read_event_into(&mut buffer)
                .map_err(|e| e.to_string())?
            {
                Event::Start(e) if e.local_name().as_ref() == b"c" => {
                    let a = attributes(&e)?;
                    coordinate = if a.get("t").is_none_or(|t| t == "n")
                        && date_styles.contains(a.get("s").map(String::as_str).unwrap_or("0"))
                    {
                        a.get("r").cloned()
                    } else {
                        None
                    };
                }
                Event::Start(e) if e.local_name().as_ref() == b"v" => in_value = true,
                Event::Text(e) if in_value => {
                    if let Some(ref coordinate) = coordinate {
                        if e.unescape()
                            .map_err(|e| e.to_string())?
                            .parse::<f64>()
                            .is_ok_and(|v| (0.0..1.0).contains(&v))
                        {
                            cells.insert((name.clone(), coordinate.clone()));
                        }
                    }
                }
                Event::End(e) if e.local_name().as_ref() == b"v" => in_value = false,
                Event::End(e) if e.local_name().as_ref() == b"c" => coordinate = None,
                Event::Eof => break,
                _ => {}
            }
            buffer.clear();
        }
    }
    Ok(cells)
}
