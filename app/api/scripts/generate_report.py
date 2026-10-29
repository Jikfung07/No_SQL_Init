from pathlib import Path
import re
import textwrap


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "rapport_no_sql.md"
OUTPUT = ROOT / "rapport_no_sql.pdf"
PAGE_WIDTH = 595
PAGE_HEIGHT = 842
LEFT = 54
TOP = 58
BOTTOM = 54
LINE_HEIGHT = 15
MAX_COLUMNS = 86


def escape_pdf_text(text: str) -> str:
    encoded = text.encode("cp1252", errors="replace")
    return encoded.hex().upper()


def markdown_lines(source: str) -> list[tuple[str, bool]]:
    result: list[tuple[str, bool]] = []
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            result.append(("", False))
            continue
        is_heading = line.startswith("#")
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = line.replace("`", "")
        line = re.sub(r"^\-\s+", "• ", line)
        wrapped = textwrap.wrap(line, width=MAX_COLUMNS, break_long_words=False) or [""]
        result.extend((part, is_heading and index == 0) for index, part in enumerate(wrapped))
    return result


def make_page(lines: list[tuple[str, bool]], page_number: int) -> bytes:
    operations = ["BT"]
    y = PAGE_HEIGHT - TOP
    for text, heading in lines:
        if not text:
            y -= LINE_HEIGHT // 2
            continue
        font = "F2" if heading else "F1"
        size = 12 if heading else 9
        operations.extend([
            f"/{font} {size} Tf",
            f"1 0 0 1 {LEFT} {y} Tm",
            f"<{escape_pdf_text(text)}> Tj",
        ])
        y -= LINE_HEIGHT + (2 if heading else 0)
    operations.extend([
        "/F1 8 Tf",
        f"1 0 0 1 {LEFT} 28 Tm",
        f"<{escape_pdf_text(f'Escales - Projet NoSQL | Page {page_number}')}> Tj",
        "ET",
    ])
    return "\n".join(operations).encode("ascii")


def make_pdf() -> bytes:
    source_lines = markdown_lines(SOURCE.read_text(encoding="utf-8"))
    usable_height = PAGE_HEIGHT - TOP - BOTTOM
    lines_per_page = usable_height // LINE_HEIGHT
    pages = [
        source_lines[start:start + lines_per_page]
        for start in range(0, len(source_lines), lines_per_page)
    ]

    objects: list[bytes] = []
    page_ids = [5 + index * 2 for index in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"))
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )

    for index, page_lines in enumerate(pages):
        page_id = page_ids[index]
        content_id = page_id + 1
        stream = make_page(page_lines, index + 1)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
            .encode("ascii")
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


if __name__ == "__main__":
    OUTPUT.write_bytes(make_pdf())
    print(f"Rapport généré : {OUTPUT}")
