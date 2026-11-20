"""PDF → page-aware chunks with bounding boxes (PyMuPDF).

Coordinates are normalized to the page (0..1, origin top-left) so the viewer can
draw highlights at any zoom. Highlight positions always come from here, never
from the model.
"""
import re
import fitz  # PyMuPDF

from . import config


def _clean(s: str) -> str:
    s = s.replace(" ", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def page_blocks(page):
    """Text blocks of one page in reading order: [(text, rect_norm)]."""
    w, h = page.rect.width, page.rect.height
    out = []
    for b in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, text, _no, btype = b[:7]
        if btype != 0:
            continue
        text = _clean(text)
        if len(text) < 2:
            continue
        out.append((text, [max(0, x0 / w), max(0, y0 / h), min(1, x1 / w), min(1, y1 / h)]))
    return out


def text_ratio(doc) -> float:
    """Average extracted characters per page; near zero means a scanned PDF."""
    n = max(1, doc.page_count)
    return sum(len(p.get_text("text").strip()) for p in doc) / n


def chunk_pdf(path, on_page=None):
    """Return list of chunks: {id, page (1-based), text, rects, blocks}.

    `blocks[i]` is the text behind `rects[i]`, used to narrow a highlight to the
    blocks an answer sentence actually draws on.

    Blocks are merged within a page until ~CHUNK_CHARS. Chunks never cross a
    page, so every citation maps to exactly one page.
    """
    doc = fitz.open(path)
    chunks = []
    for pno, page in enumerate(doc):
        buf, rects = [], []
        size = 0

        def flush():
            nonlocal buf, rects, size
            if buf:
                chunks.append({"id": len(chunks), "page": pno + 1,
                               "text": "\n".join(buf), "rects": rects, "blocks": list(buf)})
            buf, rects, size = [], [], 0

        for text, rect in page_blocks(page):
            pieces = [text]
            if len(text) > config.CHUNK_CHARS * 1.5:
                # split very long blocks at sentence-like boundaries
                pieces, cur = [], ""
                for part in re.split(r"(?<=[.다요])\s+", text):
                    if cur and len(cur) + len(part) > config.CHUNK_CHARS:
                        pieces.append(cur)
                        cur = ""
                    cur = (cur + " " + part).strip()
                if cur:
                    pieces.append(cur)
            for piece in pieces:
                if size and size + len(piece) > config.CHUNK_CHARS:
                    flush()
                buf.append(piece)
                rects.append(rect)
                size += len(piece)
        flush()
        if on_page:
            on_page(pno + 1, doc.page_count)
    n_pages = doc.page_count
    doc.close()
    return chunks, n_pages
