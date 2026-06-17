from __future__ import annotations

from pathlib import Path


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_simple_pdf(path: str | Path, title: str, sections: list[tuple[str, list[str]]]) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = [title]
    for heading, items in sections:
        lines.append("")
        lines.append(heading)
        lines.extend(items)

    page_width = 595
    page_height = 842
    left = 48
    top = 790
    line_height = 16
    bottom = 52
    pages: list[list[str]] = [[]]
    current_y = top
    for line in lines:
        if current_y < bottom:
            pages.append([])
            current_y = top
        pages[-1].append(line)
        current_y -= line_height

    objects: list[bytes] = []
    page_ids: list[int] = []

    def add_object(data: str) -> int:
        objects.append(data.encode("latin-1", errors="replace"))
        return len(objects)

    font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id_placeholder = len(objects) + 1
    objects.append(b"")

    for page_lines in pages:
        content_lines = ["BT", "/F1 11 Tf"]
        y = top
        for idx, line in enumerate(page_lines):
            escaped = _escape_pdf_text(line)
            font_size = 16 if idx == 0 else (12 if line and line == line.upper() and len(line) < 48 else 11)
            content_lines.append(f"/F1 {font_size} Tf")
            content_lines.append(f"1 0 0 1 {left} {y} Tm ({escaped}) Tj")
            y -= line_height
        content_lines.append("ET")
        content_stream = "\n".join(content_lines)
        content_id = add_object(f"<< /Length {len(content_stream.encode('latin-1', errors='replace'))} >>\nstream\n{content_stream}\nendstream")
        page_id = add_object(
            f"<< /Type /Page /Parent {pages_id_placeholder} 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id_placeholder - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")
    catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id_placeholder} 0 R >>")

    offset_table = [0]
    body = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    for idx, obj in enumerate(objects, start=1):
        offset_table.append(len(body))
        body += f"{idx} 0 obj\n".encode("latin-1") + obj + b"\nendobj\n"

    xref_offset = len(body)
    body += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    body += b"0000000000 65535 f \n"
    for offset in offset_table[1:]:
        body += f"{offset:010d} 00000 n \n".encode("latin-1")
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    target.write_bytes(body)
    return str(target)
