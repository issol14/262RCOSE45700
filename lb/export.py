"""S8: the document with its margin notes written in as PDF annotations.

Each note reads: the quoted passage, then 질문 / 필기 per turn, author "LearnBoost 필기".
A note carries its spot on the page (S4), so it is written where it belongs: a highlight over
the spot and a sticky note beside it in the margin.
"""
import fitz  # PyMuPDF

AUTHOR = "LearnBoost 필기"
YELLOW = (1.0, 0.886, 0.478)   # 형광펜 #ffe27a
GRAY = (0.80, 0.82, 0.85)      # 이해했어요


def note_text(note):
    parts = []
    if note.get("quote"):
        parts.append(note["quote"].strip())
    for it in note.get("items", []):
        parts.append(f"질문\n{it['question'].strip()}\n\n필기\n{it['text'].strip()}")
    return "\n\n".join(parts)


def annotated_pdf(pdf_path, notes) -> bytes:
    """`notes`: page (1-based), rect (0..1), quote, status, items.

    A note whose `rect` is None has no known spot (its passage could not be found on the page
    again); it becomes a sticky note stacked in the page's top-right corner."""
    doc = fitz.open(pdf_path)
    unplaced = {}
    try:
        for n in notes:
            if not (1 <= n["page"] <= doc.page_count) or not n.get("items"):
                continue
            page = doc[n["page"] - 1]
            w, h = page.rect.width, page.rect.height
            if n.get("rect") is None:
                k = unplaced[n["page"]] = unplaced.get(n["page"], 0) + 1
                text = note_text(n)
                pin = page.add_text_annot(fitz.Point(max(4, w - 28), min(h - 24, 4 + 24 * (k - 1))), text, icon="Comment")
                pin.set_info(title=AUTHOR, content=text)
                pin.set_colors(stroke=YELLOW)
                pin.update()
                continue
            x0, y0, x1, y1 = n["rect"]
            spot = fitz.Rect(x0 * w, y0 * h, x1 * w, y1 * h) & page.rect
            text = note_text(n)
            color = GRAY if n.get("status") == "understood" else YELLOW
            if not spot.is_empty:
                hl = page.add_highlight_annot(spot)
                hl.set_colors(stroke=color)
                hl.set_info(title=AUTHOR, content=text)
                hl.update()
            # sticky note in the right margin, level with the spot
            pin = page.add_text_annot(fitz.Point(min(w - 24, max(4, x1 * w + 6)), max(4, y0 * h)), text, icon="Comment")
            pin.set_info(title=AUTHOR, content=text)
            pin.set_colors(stroke=color)
            pin.update()
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
