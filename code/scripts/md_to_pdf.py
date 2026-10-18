"""One-off converter: PIPELINE_OUTPUTS.md -> PIPELINE_OUTPUTS.pdf"""
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Preformatted, KeepTogether,
)


def inline_md_to_xml(text: str) -> str:
    text = escape(text)
    # inline code spans
    text = re.sub(
        r"`([^`]+)`",
        lambda m: f'<font face="Courier" size="8.3" color="#8a2be2">{m.group(1)}</font>',
        text,
    )
    # bold (non-greedy so stray single '*' inside, e.g. from `*.py`, doesn't break the match)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return text


def parse_table(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        cells = [c.strip() for c in line.split("|")]
        rows.append(cells)
    return rows


def is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c.strip()) is not None for c in cells)


def build_story(md_path: Path):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="H1", fontName="Helvetica-Bold", fontSize=20, spaceAfter=14,
        spaceBefore=6, textColor=colors.HexColor("#1a1a2e"),
    ))
    styles.add(ParagraphStyle(
        name="H2", fontName="Helvetica-Bold", fontSize=15, spaceAfter=10,
        spaceBefore=18, textColor=colors.HexColor("#16213e"),
        borderWidth=0, borderPadding=0,
    ))
    styles.add(ParagraphStyle(
        name="H3", fontName="Helvetica-Bold", fontSize=12, spaceAfter=6,
        spaceBefore=12, textColor=colors.HexColor("#0f3460"),
    ))
    styles.add(ParagraphStyle(
        name="H4", fontName="Helvetica-Bold", fontSize=10.5, spaceAfter=4,
        spaceBefore=10, textColor=colors.HexColor("#0f3460"),
    ))
    body = ParagraphStyle(
        name="BodyText2", parent=styles["BodyText"], fontSize=9.5, leading=13,
        spaceAfter=6, alignment=TA_LEFT,
    )
    bullet = ParagraphStyle(
        name="Bullet2", parent=body, leftIndent=16, bulletIndent=4, spaceAfter=3,
    )
    cell_style = ParagraphStyle(
        name="Cell", parent=body, fontSize=8, leading=10.5, spaceAfter=0,
    )
    cell_header_style = ParagraphStyle(
        name="CellHeader", parent=cell_style, fontName="Helvetica-Bold",
        textColor=colors.white,
    )

    text = md_path.read_text(encoding="utf-8")
    replacements = {
        "—": " - ",   # em dash
        "–": "-",     # en dash
        "…": "...",   # ellipsis
        "←": "<-",    # left arrow
        "→": "->",    # right arrow
        "−": "-",     # minus sign
        "×": "x",     # multiplication sign
        "─": "-",     # box drawing horizontal
        "│": "|",     # box drawing vertical
        "├": "+",     # box drawing vertical-and-right
        "└": "+",     # box drawing up-and-right
        "▒️": "",
        "⚠️": "Note:",  # warning emoji + variation selector
        "⚠": "Note:",
        "️": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    lines = text.split("\n")

    story = []
    i = 0
    n = len(lines)

    def flush_paragraph(buf: list[str]):
        if buf:
            joined = " ".join(b.strip() for b in buf if b.strip())
            if joined:
                story.append(Paragraph(inline_md_to_xml(joined), body))
            buf.clear()

    para_buf: list[str] = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Code fence
        if stripped.startswith("```"):
            flush_paragraph(para_buf)
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            pre_style = ParagraphStyle(
                name="Code", fontName="Courier", fontSize=7.6, leading=9.5,
                backColor=colors.HexColor("#f4f4f8"), leftIndent=6,
            )
            story.append(Preformatted("\n".join(code_lines), pre_style))
            story.append(Spacer(1, 6))
            continue

        # Table block
        if stripped.startswith("|"):
            flush_paragraph(para_buf)
            table_lines = []
            while i < n and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            rows = parse_table(table_lines)
            if len(rows) >= 2 and is_separator_row(rows[1]):
                header, data_rows = rows[0], rows[2:]
            else:
                header, data_rows = rows[0], rows[1:]

            ncols = len(header)
            table_data = [[Paragraph(inline_md_to_xml(h), cell_header_style) for h in header]]
            for r in data_rows:
                r = (r + [""] * ncols)[:ncols]
                table_data.append([Paragraph(inline_md_to_xml(c), cell_style) for c in r])

            avail_width = 6.6 * inch
            first_w = avail_width * 0.22
            rest_w = (avail_width - first_w) / (ncols - 1) if ncols > 1 else avail_width
            col_widths = [first_w] + [rest_w] * (ncols - 1)

            tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16213e")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f1f7")]),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9c9d9")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 10))
            continue

        # Headings
        if stripped.startswith("#"):
            flush_paragraph(para_buf)
            level = len(re.match(r"#+", stripped).group())
            text_content = stripped[level:].strip()
            style_name = {1: "H1", 2: "H2", 3: "H3"}.get(level, "H4")
            story.append(Paragraph(inline_md_to_xml(text_content), styles[style_name]))
            if level <= 2:
                story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#c9c9d9"), spaceAfter=8))
            i += 1
            continue

        # Horizontal rule
        if re.fullmatch(r"-{3,}", stripped):
            flush_paragraph(para_buf)
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#d0d0d0")))
            story.append(Spacer(1, 4))
            i += 1
            continue

        # Bullet list
        if re.match(r"^[-*]\s+", stripped):
            flush_paragraph(para_buf)
            item_text = re.sub(r"^[-*]\s+", "", stripped)
            story.append(Paragraph("&bull;&nbsp;&nbsp;" + inline_md_to_xml(item_text), bullet))
            i += 1
            continue

        # Numbered list
        m = re.match(r"^(\d+)\.\s+(.*)", stripped)
        if m:
            flush_paragraph(para_buf)
            story.append(Paragraph(f"{m.group(1)}.&nbsp;&nbsp;" + inline_md_to_xml(m.group(2)), bullet))
            i += 1
            continue

        # Blank line -> paragraph break
        if not stripped:
            flush_paragraph(para_buf)
            i += 1
            continue

        # Regular text line, accumulate into paragraph
        para_buf.append(line)
        i += 1

    flush_paragraph(para_buf)
    return story


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("PIPELINE_OUTPUTS.md")
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".pdf")

    doc = SimpleDocTemplate(
        str(dst), pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Pipeline Outputs Reference",
    )
    story = build_story(src)
    doc.build(story)
    print(f"Wrote {dst}")


if __name__ == "__main__":
    main()
