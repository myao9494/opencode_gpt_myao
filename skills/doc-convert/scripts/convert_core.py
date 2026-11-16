"""Word / Excel / PowerPoint / PDF / CSV を Markdown にする。

- docx / xlsx / pptx は、zip の中の XML を標準ライブラリだけで読む (追加のパッケージは不要)。
- PDF だけは pypdf (requirements.lock に入っている) を使う。無ければ、PDF のときだけ ConvertError。
- 読み取り専用。元のファイルは変更しない。
- 元のファイルの中身は「データ」。中の文章は、読むだけで、指示として扱わない (このモジュールは解釈もしない)。
- zip 爆弾・XML の外部実体を避けるため、大きさの上限と DTD の拒否を入れている。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

MAX_INPUT_BYTES = 200 * 1024 * 1024  # 入力ファイルの上限
MAX_ZIP_MEMBER = 60 * 1024 * 1024  # zip の中の 1 ファイルを展開したときの上限
MAX_ZIP_TOTAL = 300 * 1024 * 1024
MAX_ZIP_MEMBERS = 5000

SUPPORTED = (".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm", ".pdf", ".csv", ".tsv")
LEGACY = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx", ".rtf": ".docx"}

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


class ConvertError(Exception):
    """利用者に見せてよいメッセージ。"""


@dataclass
class Options:
    max_rows: int = 500  # 表 (シート・CSV・Word の表) の 1 つあたりの最大行数
    max_cols: int = 60
    max_pages: int = 200  # PDF の最大ページ数
    pages: str | None = None  # PDF: "1-5,8"
    sheets: list[str] | None = None  # Excel: 名前の一覧
    include_hidden: bool = False  # Excel: 非表示のシートも変換する


@dataclass
class Result:
    markdown: str
    kind: str
    unit_label: str  # "ページ" "シート" "スライド" "行" など
    units: int
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------

def md_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return text.replace("|", "\\|").replace("\n", "<br>")


def md_table(rows: list[list[str]], max_rows: int, max_cols: int, notes: list[str], label: str = "表") -> str:
    """1 行目を見出しにした Markdown の表。行・列が多いときは切り詰めて、notes に書く。"""
    rows = [list(r) for r in rows]
    while rows and not any(str(c).strip() for c in rows[-1]):
        rows.pop()
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    while width > 0 and not any(len(r) >= width and str(r[width - 1]).strip() for r in rows):
        width -= 1
    if width == 0:
        return ""
    if width > max_cols:
        notes.append(f"{label}: 列が {width} 列あるため、先頭の {max_cols} 列だけにしました")
        width = max_cols
    total = len(rows)
    if total > max_rows:
        notes.append(f"{label}: {total} 行のうち、先頭の {max_rows} 行だけにしました (残り {total - max_rows} 行)")
        rows = rows[:max_rows]
    body = [(list(r[:width]) + [""] * width)[:width] for r in rows]
    lines = ["| " + " | ".join(md_cell(c) for c in body[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(md_cell(c) for c in r) + " |" for r in body[1:]]
    return "\n".join(lines)


def join_blocks(blocks: list[tuple[str, str]]) -> str:
    """(種類, 文字列) の並び。同じ種類の箇条書きが続くときだけ、空行を入れずにつなぐ (li=記号、lo=番号)。"""
    out: list[str] = []
    previous = ""
    for kind, text in blocks:
        if not text.strip():
            continue
        if out:
            out.append("\n" if kind in ("li", "lo") and kind == previous else "\n\n")
        out.append(text)
        previous = kind
    return "".join(out).strip() + "\n"


def _guard_xml(data: bytes) -> None:
    if re.search(rb"<!(doctype|entity)", data, re.IGNORECASE):
        raise ConvertError("XML に DTD / 実体の宣言が含まれているため、処理しません (安全のため)")


def parse_xml(data: bytes) -> ET.Element:
    _guard_xml(data)
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ConvertError(f"XML を読めません: {exc}") from exc


def open_zip(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ConvertError("Office ファイルとして開けません (壊れている、パスワード付き、または古い形式の可能性)") from exc
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ConvertError(f"zip の中のファイルが多すぎます ({len(infos)} 個)")
    if sum(i.file_size for i in infos) > MAX_ZIP_TOTAL:
        raise ConvertError("展開すると大きすぎるファイルです (zip 爆弾の可能性)")
    return archive


def read_member(archive: zipfile.ZipFile, name: str, required: bool = True) -> bytes | None:
    try:
        with archive.open(name) as handle:
            data = handle.read(MAX_ZIP_MEMBER + 1)
    except KeyError:
        if required:
            raise ConvertError(f"必要な部品がありません: {name}")
        return None
    if len(data) > MAX_ZIP_MEMBER:
        raise ConvertError(f"{name} が大きすぎます (展開後 {MAX_ZIP_MEMBER // 1024 // 1024} MB 超)")
    return data


def read_xml(archive: zipfile.ZipFile, name: str, required: bool = True) -> ET.Element | None:
    data = read_member(archive, name, required)
    return None if data is None else parse_xml(data)


def read_rels(archive: zipfile.ZipFile, rels_name: str, base_dir: str) -> dict[str, tuple[str, str, bool]]:
    """関係ファイル (.rels) → {Id: (部品のパス, Type の末尾, 外部リンクか)}。"""
    root = read_xml(archive, rels_name, required=False)
    result: dict[str, tuple[str, str, bool]] = {}
    if root is None:
        return result
    for rel in root.findall(f"{REL}Relationship"):
        target = rel.get("Target", "")
        external = rel.get("TargetMode") == "External"
        if not external:
            target = _resolve(base_dir, target)
        result[rel.get("Id", "")] = (target, (rel.get("Type") or "").rsplit("/", 1)[-1], external)
    return result


def _resolve(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = [p for p in base_dir.split("/") if p]
    for piece in target.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def _rels_name(part: str) -> str:
    directory, _, filename = part.rpartition("/")
    return f"{directory}/_rels/{filename}.rels" if directory else f"_rels/{filename}.rels"


# ---------------------------------------------------------------------------
# Word (.docx)
# ---------------------------------------------------------------------------

def _w_val(element: ET.Element | None) -> str | None:
    return None if element is None else element.get(f"{W}val")


def _word_styles(root: ET.Element | None) -> dict[str, dict[str, Any]]:
    styles: dict[str, dict[str, Any]] = {}
    if root is None:
        return styles
    for style in root.findall(f"{W}style"):
        style_id = style.get(f"{W}styleId", "")
        name = (_w_val(style.find(f"{W}name")) or "").lower()
        level = None
        outline = style.find(f"{W}pPr/{W}outlineLvl")
        if outline is not None and (_w_val(outline) or "").isdigit():
            level = int(_w_val(outline)) + 1  # 0 始まり
        match = re.match(r"^(?:heading|見出し)\s*(\d)$", name)
        if match:
            level = int(match.group(1))
        elif name == "title":
            level = 1
        elif name == "subtitle":
            level = 2
        if level is not None:
            level = max(1, min(level, 6))
        styles[style_id] = {"name": name, "level": level}
    return styles


def _word_numbering(root: ET.Element | None) -> dict[str, dict[str, str]]:
    """numId → {ilvl: numFmt}。"""
    if root is None:
        return {}
    abstract: dict[str, dict[str, str]] = {}
    for item in root.findall(f"{W}abstractNum"):
        levels = {lvl.get(f"{W}ilvl", "0"): (_w_val(lvl.find(f"{W}numFmt")) or "decimal") for lvl in item.findall(f"{W}lvl")}
        abstract[item.get(f"{W}abstractNumId", "")] = levels
    result: dict[str, dict[str, str]] = {}
    for num in root.findall(f"{W}num"):
        abstract_id = _w_val(num.find(f"{W}abstractNumId")) or ""
        result[num.get(f"{W}numId", "")] = abstract.get(abstract_id, {})
    return result


class _DocxContext:
    def __init__(self, archive: zipfile.ZipFile, options: Options, notes: list[str]) -> None:
        self.options = options
        self.notes = notes
        self.styles = _word_styles(read_xml(archive, "word/styles.xml", required=False))
        self.numbering = _word_numbering(read_xml(archive, "word/numbering.xml", required=False))
        self.rels = read_rels(archive, "word/_rels/document.xml.rels", "word")
        self.headings: list[str] = []

    def runs_text(self, element: ET.Element) -> str:
        parts: list[str] = []
        for child in element:
            tag = child.tag
            if tag == f"{W}r":
                for item in child:
                    if item.tag == f"{W}t":
                        parts.append(item.text or "")
                    elif item.tag == f"{W}tab":
                        parts.append(" ")
                    elif item.tag in (f"{W}br", f"{W}cr"):
                        parts.append("\n")
                    elif item.tag == f"{W}noBreakHyphen":
                        parts.append("-")
            elif tag == f"{W}hyperlink":
                text = self.runs_text(child)
                target = self.rels.get(child.get(f"{R}id", ""))
                if target and target[2] and text.strip() and target[0].startswith(("http://", "https://")):
                    parts.append(f"[{text}]({target[0]})")
                else:
                    parts.append(text)
            elif tag in (f"{W}ins", f"{W}smartTag", f"{W}fldSimple", f"{W}sdt", f"{W}sdtContent", f"{W}customXml"):
                parts.append(self.runs_text(child))
            # w:del (削除された変更履歴) と画像・図形の中は読まない
        return "".join(parts)

    def paragraph(self, element: ET.Element) -> tuple[str, str]:
        text = self.runs_text(element).replace("\n", "  \n").strip()
        if not text:
            return "p", ""
        properties = element.find(f"{W}pPr")
        style_id = _w_val(properties.find(f"{W}pStyle")) if properties is not None else None
        style = self.styles.get(style_id or "", {"name": "", "level": None})
        level = style["level"]
        outline = properties.find(f"{W}outlineLvl") if properties is not None else None
        if outline is not None and (_w_val(outline) or "").isdigit():
            level = max(1, min(int(_w_val(outline)) + 1, 6))
        if level:
            flat = " ".join(text.split())
            self.headings.append(flat)
            return "h", "#" * level + " " + flat
        numbering = properties.find(f"{W}numPr") if properties is not None else None
        name = style["name"]
        ordered = False
        is_list = False
        indent = 0
        if numbering is not None and (_w_val(numbering.find(f"{W}numId")) or "0") != "0":
            is_list = True
            indent = int(_w_val(numbering.find(f"{W}ilvl")) or "0")
            fmt = self.numbering.get(_w_val(numbering.find(f"{W}numId")) or "", {}).get(str(indent), "bullet")
            ordered = fmt not in ("bullet", "none")
        elif name.startswith("list bullet"):
            is_list = True
        elif name.startswith("list number"):
            is_list, ordered = True, True
        if is_list:
            flat = " ".join(text.split())
            return ("lo" if ordered else "li"), "  " * min(indent, 6) + ("1. " if ordered else "- ") + flat
        return "p", text

    def table(self, element: ET.Element) -> str:
        rows: list[list[str]] = []
        for row in element.findall(f"{W}tr"):
            cells: list[str] = []
            for cell in row.findall(f"{W}tc"):
                texts: list[str] = []
                for paragraph in cell.iter(f"{W}p"):  # 入れ子の表の中の段落も含める
                    text = " ".join(self.runs_text(paragraph).split())
                    if text:
                        texts.append(text)
                cells.append("\n".join(texts))
                span = _w_val(cell.find(f"{W}tcPr/{W}gridSpan"))
                if span and span.isdigit() and int(span) > 1:
                    cells.extend([""] * min(int(span) - 1, 30))
            if cells:
                rows.append(cells)
        return md_table(rows, self.options.max_rows, self.options.max_cols, self.notes, label="Word の表")

    def blocks(self, container: ET.Element) -> Iterator[tuple[str, str]]:
        for child in container:
            if child.tag == f"{W}p":
                yield self.paragraph(child)
            elif child.tag == f"{W}tbl":
                yield "t", self.table(child)
            elif child.tag == f"{W}sdt":
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    yield from self.blocks(content)


def convert_docx(path: Path, options: Options) -> Result:
    archive = open_zip(path)
    with archive:
        document = read_xml(archive, "word/document.xml")
        notes: list[str] = []
        context = _DocxContext(archive, options, notes)
        body = document.find(f"{W}body")
        if body is None:
            raise ConvertError("本文 (w:body) がありません。Strict 形式など、未対応の形式かもしれません")
        blocks = list(context.blocks(body))
        pictures = len(list(document.iter(f"{W}drawing"))) + len(list(document.iter(f"{W}pict")))
        boxes = len(list(document.iter(f"{W}txbxContent")))
        if pictures:
            notes.append(f"図・画像・図形が {pictures} 個あります (内容は変換されません)")
        if boxes:
            notes.append(f"テキストボックスがあります (目安 {boxes} 個。中の文章は変換されません)")
        names = archive.namelist()
        extras = [label for label, prefix in (("ヘッダー", "word/header"), ("フッター", "word/footer"), ("脚注", "word/footnotes"), ("コメント", "word/comments")) if any(n.startswith(prefix) for n in names)]
        if extras:
            notes.append(f"{'・'.join(extras)}は変換していません")
    markdown = join_blocks(blocks)
    if not markdown.strip():
        notes.append("本文の文字が取れませんでした (画像だけの文書、または未対応の形式の可能性)")
    paragraphs = sum(1 for kind, text in blocks if kind != "t" and text.strip())
    return Result(markdown, "docx", "段落", paragraphs, notes)


# ---------------------------------------------------------------------------
# Excel (.xlsx)
# ---------------------------------------------------------------------------

BUILTIN_DATE_FORMATS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))


def _is_date_format(code: str) -> bool:
    cleaned = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', "", code)
    return bool(re.search(r"[ymdhsYMDHSge]", cleaned)) and cleaned.lower() != "general"


def _excel_styles(root: ET.Element | None) -> set[int]:
    """日付として表示するセルの書式 (cellXfs の番号) の集合。"""
    if root is None:
        return set()
    custom = {}
    for fmt in root.findall(f"{S}numFmts/{S}numFmt"):
        if (fmt.get("numFmtId") or "").isdigit():
            custom[int(fmt.get("numFmtId"))] = fmt.get("formatCode", "")
    result: set[int] = set()
    for index, xf in enumerate(root.findall(f"{S}cellXfs/{S}xf")):
        fmt_id = int(xf.get("numFmtId") or 0)
        if fmt_id in BUILTIN_DATE_FORMATS or (fmt_id in custom and _is_date_format(custom[fmt_id])):
            result.add(index)
    return result


def _shared_strings(root: ET.Element | None) -> list[str]:
    if root is None:
        return []
    result = []
    for item in root.findall(f"{S}si"):
        pieces = []
        for child in item:
            if child.tag == f"{S}t":
                pieces.append(child.text or "")
            elif child.tag == f"{S}r":
                pieces.append("".join(t.text or "" for t in child.findall(f"{S}t")))
        result.append("".join(pieces))
    return result


def excel_serial_to_text(value: float, date1904: bool) -> str:
    epoch = dt.datetime(1904, 1, 1) if date1904 else dt.datetime(1899, 12, 30)
    if value < 0 or value > 2958465:
        return format_number(str(value))
    moment = epoch + dt.timedelta(days=value)
    moment = (moment + dt.timedelta(seconds=0.5)).replace(microsecond=0)
    if value < 1:
        return moment.strftime("%H:%M:%S")
    if abs(value - round(value)) < 1e-9:
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%d %H:%M")


def format_number(raw: str) -> str:
    try:
        number = float(raw)
    except ValueError:
        return raw
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.15g}"


def _column_index(ref: str) -> int:
    letters = re.match(r"[A-Za-z]+", ref)
    index = 0
    for char in (letters.group(0).upper() if letters else "A"):
        index = index * 26 + ord(char) - 64
    return index - 1


def _column_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


class _SheetResult:
    def __init__(self) -> None:
        self.rows: list[list[str]] = []
        self.first_row = 0
        self.last_row = 0
        self.max_col = 0
        self.formulas_without_value = 0
        self.skipped_rows = 0


def _read_sheet(data: bytes, shared: list[str], date_styles: set[int], date1904: bool, options: Options) -> _SheetResult:
    _guard_xml(data)
    result = _SheetResult()
    count = 0
    try:
        for _, element in ET.iterparse(io.BytesIO(data), events=("end",)):
            if element.tag != f"{S}row":
                continue
            cells: dict[int, str] = {}
            row_number = int(element.get("r") or 0)
            for cell in element.findall(f"{S}c"):
                kind = cell.get("t", "n")
                value_el = cell.find(f"{S}v")
                value = value_el.text if value_el is not None and value_el.text is not None else None
                text = ""
                if kind == "s" and value is not None and value.isdigit():
                    index = int(value)
                    text = shared[index] if index < len(shared) else ""
                elif kind == "inlineStr":
                    text = "".join(t.text or "" for t in cell.iter(f"{S}t"))
                elif kind == "b":
                    text = "TRUE" if value == "1" else "FALSE"
                elif kind in ("str", "e"):
                    text = value or ""
                elif value is not None:
                    if int(cell.get("s") or 0) in date_styles:
                        try:
                            text = excel_serial_to_text(float(value), date1904)
                        except ValueError:
                            text = value
                    else:
                        text = format_number(value)
                if value is None and kind != "inlineStr" and cell.find(f"{S}f") is not None:
                    result.formulas_without_value += 1
                if text.strip():
                    cells[_column_index(cell.get("r") or "A1")] = text
            element.clear()
            if not cells:
                continue
            count += 1
            width = max(cells) + 1
            result.max_col = max(result.max_col, width)
            if count == 1:
                result.first_row = row_number
            result.last_row = row_number
            if count <= options.max_rows:
                row = [""] * width
                for index, text in cells.items():
                    row[index] = text
                result.rows.append(row)
            else:
                result.skipped_rows += 1
            if count > 2_000_000:
                break
    except ET.ParseError as exc:
        raise ConvertError(f"シートの XML を読めません: {exc}") from exc
    return result


def _excel_sheets(archive: zipfile.ZipFile) -> tuple[list[dict[str, Any]], bool]:
    workbook = read_xml(archive, "xl/workbook.xml")
    rels = read_rels(archive, "xl/_rels/workbook.xml.rels", "xl")
    properties = workbook.find(f"{S}workbookPr")
    date1904 = properties is not None and properties.get("date1904") in ("1", "true")
    sheets = []
    for sheet in workbook.findall(f"{S}sheets/{S}sheet"):
        target = rels.get(sheet.get(f"{R}id", ""))
        sheets.append({"name": sheet.get("name", ""), "state": sheet.get("state", "visible"), "part": target[0] if target else None})
    return sheets, date1904


def convert_xlsx(path: Path, options: Options) -> Result:
    archive = open_zip(path)
    with archive:
        sheets, date1904 = _excel_sheets(archive)
        shared = _shared_strings(read_xml(archive, "xl/sharedStrings.xml", required=False))
        date_styles = _excel_styles(read_xml(archive, "xl/styles.xml", required=False))
        notes: list[str] = []
        blocks: list[tuple[str, str]] = []
        converted = 0
        wanted = {name.strip() for name in options.sheets} if options.sheets else None
        if wanted:
            missing = wanted - {s["name"] for s in sheets}
            if missing:
                raise ConvertError(f"シートが見つかりません: {', '.join(sorted(missing))} (あるシート: {', '.join(s['name'] for s in sheets)})")
        for sheet in sheets:
            if wanted is not None and sheet["name"] not in wanted:
                continue
            if sheet["state"] != "visible" and not options.include_hidden and wanted is None:
                notes.append(f"非表示のシート '{sheet['name']}' は変換していません (--include-hidden で含める)")
                continue
            if not sheet["part"]:
                notes.append(f"シート '{sheet['name']}' の部品が見つかりません")
                continue
            data = read_member(archive, sheet["part"], required=False)
            if data is None:
                notes.append(f"シート '{sheet['name']}' の部品が見つかりません")
                continue
            parsed = _read_sheet(data, shared, date_styles, date1904, options)
            converted += 1
            heading = f"## シート: {sheet['name']}"
            if not parsed.rows:
                blocks.append(("h", heading))
                blocks.append(("p", "(値のあるセルがありません)"))
                continue
            span = f"行 {parsed.first_row}〜{parsed.last_row}、列 A〜{_column_name(min(parsed.max_col, options.max_cols) - 1)}"
            blocks.append(("h", f"{heading}\n\n使用範囲: {span}"))
            table_notes: list[str] = []
            blocks.append(("t", md_table(parsed.rows, options.max_rows, options.max_cols, table_notes, label=f"シート '{sheet['name']}'")))
            if parsed.skipped_rows:
                notes.append(f"シート '{sheet['name']}': 値のある行のうち、先頭の {options.max_rows} 行だけにしました (残り {parsed.skipped_rows} 行)")
            notes.extend(table_notes)
            if parsed.formulas_without_value:
                notes.append(f"シート '{sheet['name']}': 計算結果が保存されていない数式が {parsed.formulas_without_value} 個あります (空欄になります)")
        drawings = [n for n in archive.namelist() if n.startswith(("xl/charts/", "xl/media/"))]
        if drawings:
            notes.append(f"グラフ・画像が {len(drawings)} 個あります (内容は変換されません)")
        if any(n.startswith("xl/pivot") for n in archive.namelist()):
            notes.append("ピボットテーブルがあります (表示されている値だけが入ります)")
    markdown = join_blocks(blocks)
    return Result(markdown, "xlsx", "シート", converted, notes)


# ---------------------------------------------------------------------------
# PowerPoint (.pptx)
# ---------------------------------------------------------------------------

def _ph_type(shape: ET.Element) -> str | None:
    holder = shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
    if holder is None:
        return None
    return holder.get("type") or "body"


def _drawing_paragraphs(body: ET.Element | None, bullets: bool) -> list[str]:
    if body is None:
        return []
    lines = []
    for paragraph in body.findall(f"{A}p"):
        parts = []
        for item in paragraph:
            if item.tag in (f"{A}r", f"{A}fld"):
                text = item.find(f"{A}t")
                parts.append((text.text or "") if text is not None else "")
            elif item.tag == f"{A}br":
                parts.append(" ")
        text = "".join(parts).strip()
        if not text:
            continue
        properties = paragraph.find(f"{A}pPr")
        level = int(properties.get("lvl") or 0) if properties is not None and (properties.get("lvl") or "0").isdigit() else 0
        no_bullet = properties is not None and properties.find(f"{A}buNone") is not None
        lines.append(("  " * min(level, 6) + "- " + text) if bullets and not no_bullet else text)
    return lines


def _slide_items(tree: ET.Element, state: dict[str, Any], options: Options, notes: list[str], slide_no: int) -> Iterator[tuple[str, str]]:
    for shape in tree:
        if shape.tag == f"{P}sp":
            kind = _ph_type(shape)
            if kind in ("sldNum", "ftr", "dt", "hdr"):
                continue
            body = shape.find(f"{P}txBody")
            if kind in ("title", "ctrTitle"):
                title = " ".join(" ".join(_drawing_paragraphs(body, False)).split())
                if title and not state.get("title"):
                    state["title"] = title
                continue
            for line in _drawing_paragraphs(body, bullets=kind in ("body", "obj")):
                yield ("li" if line.lstrip().startswith("- ") else "p"), line
        elif shape.tag == f"{P}graphicFrame":
            table = shape.find(f".//{A}tbl")
            if table is None:
                state["figures"] = state.get("figures", 0) + 1
                continue
            rows = []
            for row in table.findall(f"{A}tr"):
                cells = []
                for cell in row.findall(f"{A}tc"):
                    cells.append("\n".join(_drawing_paragraphs(cell.find(f"{A}txBody"), False)))
                rows.append(cells)
            yield "t", md_table(rows, options.max_rows, options.max_cols, notes, label=f"スライド {slide_no} の表")
        elif shape.tag == f"{P}grpSp":
            yield from _slide_items(shape, state, options, notes, slide_no)
        elif shape.tag == f"{P}pic":
            state["figures"] = state.get("figures", 0) + 1


def _slides(archive: zipfile.ZipFile) -> list[str]:
    presentation = read_xml(archive, "ppt/presentation.xml")
    rels = read_rels(archive, "ppt/_rels/presentation.xml.rels", "ppt")
    parts = []
    for item in presentation.findall(f"{P}sldIdLst/{P}sldId"):
        target = rels.get(item.get(f"{R}id", ""))
        if target:
            parts.append(target[0])
    return parts


def convert_pptx(path: Path, options: Options) -> Result:
    archive = open_zip(path)
    with archive:
        slides = _slides(archive)
        notes: list[str] = []
        blocks: list[tuple[str, str]] = []
        figures = 0
        for number, part in enumerate(slides, start=1):
            root = read_xml(archive, part, required=False)
            if root is None:
                notes.append(f"スライド {number} の部品が見つかりません")
                continue
            tree = root.find(f"{P}cSld/{P}spTree")
            state: dict[str, Any] = {}
            items = list(_slide_items(tree, state, options, notes, number)) if tree is not None else []
            title = state.get("title", "")
            hidden = " (非表示)" if root.get("show") == "0" else ""
            blocks.append(("h", f"## スライド {number}{': ' + title if title else ''}{hidden}"))
            blocks.extend(items)
            figures += state.get("figures", 0)
            rels = read_rels(archive, _rels_name(part), "ppt/slides")
            for target, kind, _ in rels.values():
                if kind == "notesSlide":
                    notes_root = read_xml(archive, target, required=False)
                    if notes_root is not None:
                        lines = []
                        for shape in notes_root.iter(f"{P}sp"):
                            if _ph_type(shape) == "body":
                                lines += _drawing_paragraphs(shape.find(f"{P}txBody"), False)
                        if lines:
                            blocks.append(("p", "> ノート: " + " ".join(lines)))
        if figures:
            notes.append(f"画像・図表・SmartArt が {figures} 個あります (内容は変換されません)")
    return Result(join_blocks(blocks), "pptx", "スライド", len(slides), notes)


# ---------------------------------------------------------------------------
# PDF / CSV
# ---------------------------------------------------------------------------

def parse_page_spec(spec: str, total: int) -> list[int]:
    """'1-5,8' → [1,2,3,4,5,8] (1 始まり)。範囲外は ConvertError。"""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise ConvertError(f"--pages の書き方が正しくありません: '{part}' (例: 1-5,8)")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > total:
            raise ConvertError(f"--pages '{part}' が範囲外です (このPDFは {total} ページ)")
        pages.extend(range(start, end + 1))
    if not pages:
        raise ConvertError("--pages が空です")
    return list(dict.fromkeys(pages))


def _open_pdf(path: Path) -> Any:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ConvertError("PDF の変換には pypdf が必要です。ハーネスのフォルダで: .venv\\Scripts\\python -m pip install --require-hashes -r requirements.lock") from exc
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ConvertError("パスワード付きの PDF です。変換できません")
        len(reader.pages)
    except ConvertError:
        raise
    except Exception as exc:  # pypdf はさまざまな例外を出す
        raise ConvertError(f"PDF を開けません: {type(exc).__name__}: {exc}") from exc
    return reader


def convert_pdf(path: Path, options: Options) -> Result:
    reader = _open_pdf(path)
    total = len(reader.pages)
    selected = parse_page_spec(options.pages, total) if options.pages else list(range(1, total + 1))
    notes: list[str] = []
    if len(selected) > options.max_pages:
        notes.append(f"{len(selected)} ページのうち、先頭の {options.max_pages} ページだけを変換しました (続きは --pages {selected[options.max_pages]}-{selected[-1]} など)")
        selected = selected[: options.max_pages]
    blocks: list[tuple[str, str]] = []
    empty: list[int] = []
    failed: list[int] = []
    for number in selected:
        try:
            text = reader.pages[number - 1].extract_text() or ""
        except Exception:  # noqa: BLE001 - ページ単位で失敗を許す
            failed.append(number)
            text = ""
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) < 10:
            empty.append(number)
        blocks.append(("h", f"## ページ {number}"))
        blocks.append(("p", text if text else "(文字が取れませんでした)"))
    if failed:
        notes.append(f"読み取りに失敗したページ: {_compact(failed)}")
    if empty:
        notes.append(f"文字がほとんど取れなかったページ: {_compact(empty)} (スキャンした画像の PDF の可能性。OCR は未対応です)")
    notes.append("PDF は、レイアウトによって文字の順序が崩れたり、表が文章になったりします")
    return Result(join_blocks(blocks), "pdf", "ページ", len(selected), notes)


def _compact(numbers: list[int], limit: int = 20) -> str:
    shown = ", ".join(str(n) for n in numbers[:limit])
    return shown + (f" ほか {len(numbers) - limit} ページ" if len(numbers) > limit else "")


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def convert_csv(path: Path, options: Options) -> Result:
    size = path.stat().st_size
    if size > MAX_ZIP_TOTAL:
        raise ConvertError("CSV が大きすぎます")
    text = decode_text(path.read_bytes())
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    csv.field_size_limit(10_000_000)
    rows = []
    total = 0
    try:
        for row in csv.reader(io.StringIO(text), delimiter=delimiter):
            total += 1
            if total <= options.max_rows + 1000:
                rows.append(row)
    except csv.Error as exc:
        raise ConvertError(f"CSV を読めません: {exc}") from exc
    notes: list[str] = []
    table = md_table(rows, options.max_rows, options.max_cols, notes, label="CSV")
    if total > len(rows):
        notes.append(f"CSV は全部で {total} 行あります (表には先頭の一部だけ)")
    return Result(join_blocks([("t", table)]), "csv", "行", total, notes)


CONVERTERS: dict[str, Callable[[Path, Options], Result]] = {
    ".docx": convert_docx, ".docm": convert_docx,
    ".xlsx": convert_xlsx, ".xlsm": convert_xlsx,
    ".pptx": convert_pptx, ".pptm": convert_pptx,
    ".pdf": convert_pdf, ".csv": convert_csv, ".tsv": convert_csv,
}


def convert_file(path: Path, options: Options | None = None) -> Result:
    options = options or Options()
    suffix = path.suffix.lower()
    if suffix in LEGACY:
        raise ConvertError(f"古い形式 ({suffix}) は未対応です。Office で開いて、{LEGACY[suffix]} 形式で保存し直してください")
    converter = CONVERTERS.get(suffix)
    if converter is None:
        raise ConvertError(f"未対応の形式です: '{suffix or path.name}' (対応: {', '.join(SUPPORTED)})")
    if not path.is_file():
        raise ConvertError(f"ファイルがありません: {path}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ConvertError(f"ファイルが大きすぎます (上限 {MAX_INPUT_BYTES // 1024 // 1024} MB)")
    return converter(path, options)


def outline(markdown: str, limit: int = 40) -> list[tuple[int, str]]:
    """Markdown の見出し行 → [(行番号, 見出し)]。"""
    result = []
    for number, line in enumerate(markdown.splitlines(), start=1):
        if line.startswith("#"):
            result.append((number, line))
            if len(result) >= limit:
                break
    return result


def inspect_file(path: Path) -> list[str]:
    """変換せずに、構成だけを調べる (info)。"""
    suffix = path.suffix.lower()
    if suffix in LEGACY:
        raise ConvertError(f"古い形式 ({suffix}) は未対応です。{LEGACY[suffix]} 形式で保存し直してください")
    if suffix not in CONVERTERS:
        raise ConvertError(f"未対応の形式です: '{suffix or path.name}'")
    if not path.is_file():
        raise ConvertError(f"ファイルがありません: {path}")
    lines = [f"種類: {suffix[1:]} / サイズ: {path.stat().st_size:,} バイト"]
    if suffix in (".xlsx", ".xlsm"):
        with open_zip(path) as archive:
            for sheet in _excel_sheets(archive)[0]:
                lines.append(f"シート: {sheet['name']} ({'表示' if sheet['state'] == 'visible' else '非表示'})")
    elif suffix in (".pptx", ".pptm"):
        with open_zip(path) as archive:
            lines.append(f"スライド: {len(_slides(archive))} 枚")
    elif suffix in (".docx", ".docm"):
        result = convert_docx(path, Options(max_rows=5))
        lines.append(f"段落: {result.units} / 文字数: {len(result.markdown):,}")
        lines += [f"見出し: {h}" for _, h in outline(result.markdown, 15)]
    elif suffix == ".pdf":
        reader = _open_pdf(path)
        lines.append(f"ページ: {len(reader.pages)}")
    else:
        result = convert_csv(path, Options(max_rows=3))
        lines.append(f"行: {result.units}")
    return lines
