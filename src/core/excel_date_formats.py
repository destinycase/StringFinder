"""Lazy XLSX date-format metadata for ambiguous serial-zero/time values."""
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET


def date_format(code):
    code = re.sub(r'"[^"]*"|[\\_*].|\[[^\]]*\]', '', code).lower()
    return 'y' in code or 'd' in code


def read_date_cells(path, sheet_name):
    cells = set()
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        namespace = workbook.tag.split('}')[0][1:]
        ns = {'s': namespace}
        properties = workbook.find('s:workbookPr', ns)
        if properties is None or properties.get('date1904', '0') not in ('1', 'true'):
            return cells
        styles = ET.fromstring(archive.read('xl/styles.xml'))
        ns = {'s': styles.tag.split('}')[0][1:]}
        custom = {int(e.attrib['numFmtId']): e.attrib['formatCode'] for e in styles.findall('s:numFmts/s:numFmt', ns)}
        date_styles = set()
        for index, xf in enumerate(styles.findall('s:cellXfs/s:xf', ns)):
            fmt = int(xf.get('numFmtId', '0'))
            if (14 <= fmt <= 17 or fmt == 22 or fmt in (27, 28, 29, 30, 31, 34, 35, 36) or 50 <= fmt <= 58
                    or date_format(custom.get(fmt, ''))):
                date_styles.add(str(index))
        relations = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {e.attrib['Id']: e.attrib['Target'] for e in relations}
        for sheet in workbook.findall('s:sheets/s:sheet', ns):
            # Another damaged worksheet must not invalidate this sheet's dates.
            if sheet.attrib['name'] != sheet_name:
                continue
            relation = next(v for k, v in sheet.attrib.items() if k.endswith('}id'))
            target = targets[relation]
            member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            with archive.open(member) as stream:
                for _, element in ET.iterparse(stream, events=('end',)):
                    if element.tag.endswith('}c'):
                        if element.get('s', '0') in date_styles and element.get('t', 'n') == 'n':
                            value = element.find('s:v', ns)
                            if value is not None and value.text is not None and 0 <= float(value.text) < 1:
                                cells.add((sheet.attrib['name'], element.attrib['r']))
                        element.clear()
                    elif element.tag.endswith('}row'):
                        element.clear()
    return cells
