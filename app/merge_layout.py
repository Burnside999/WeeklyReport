"""Read only worksheet merge geometry from official XLSX exports (no cell contents retained)."""
import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from .core import column

S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
P = '{http://schemas.openxmlformats.org/package/2006/relationships}'


def parse_layout(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            if sum(f.file_size for f in z.infolist()) > 100 * 1024 * 1024:
                raise ValueError('导出表格解压后过大（最多 100 MiB）')
            def xml(path):
                raw = z.read(path)
                if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
                    raise ValueError('不支持带实体声明的 XML')
                return ET.fromstring(raw)
            rels = {r.attrib['Id']:r.attrib['Target'] for r in xml('xl/_rels/workbook.xml.rels').findall(P+'Relationship') if r.attrib.get('TargetMode') != 'External'}
            output = {}
            for sheet in xml('xl/workbook.xml').findall(S+'sheets/'+S+'sheet'):
                target = rels[sheet.attrib[R+'id']]
                path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/'+target)
                if not path.startswith('xl/'):
                    raise ValueError('导出表格路径异常')
                merges = []
                for m in xml(path).findall(S+'mergeCells/'+S+'mergeCell'):
                    match = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d+)',m.attrib['ref'])
                    if not match:
                        raise ValueError('合并区域格式异常')
                    a,b,c,d = match.groups()
                    region = [int(b),int(d),column(a),column(c)]
                    if region[0]<1 or region[1]<region[0] or region[3]<region[2]:
                        raise ValueError('合并区域范围异常')
                    merges.append(region)
                if sheet.attrib['name'] in output:
                    raise ValueError('导出表格包含重名 Sheet')
                output[sheet.attrib['name']] = merges
            if not output:
                raise ValueError('导出文件没有工作表')
            return output
    except (KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ValueError('无法识别官方导出的 XLSX 合并结构') from exc
