"""S5: a region the learner dragged on a page (a table, a figure) → image for a vision model.

The region arrives with the question as page + normalized x, y, width, height, and is
rendered with PyMuPDF, the parser the rest of the app uses.
"""
import base64

import fitz  # PyMuPDF

MAX_SIDE = 1024   # longest side of the image sent to the model, px


def valid(region) -> bool:
    try:
        x, y, w, h = (float(region[k]) for k in ("x", "y", "width", "height"))
    except (TypeError, KeyError, ValueError):
        return False
    return x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= 1.000001 and y + h <= 1.000001


def rect_of(region):
    """Normalized [x0, y0, x1, y1], the form selections and notes use."""
    return [region["x"], region["y"], min(1.0, region["x"] + region["width"]), min(1.0, region["y"] + region["height"])]


def _clip(page, region):
    w, h = page.rect.width, page.rect.height
    x0, y0, x1, y1 = rect_of(region)
    return fitz.Rect(x0 * w, y0 * h, x1 * w, y1 * h)


def render(pdf_path, page_no, region):
    """Return (JPEG data URL of the region, text PyMuPDF finds inside it)."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_no - 1]
        clip = _clip(page, region)
        scale = min(3.0, MAX_SIDE / max(clip.width, clip.height, 1))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        data = base64.b64encode(pix.tobytes("jpeg", jpg_quality=85)).decode()
        text = " ".join(page.get_text("text", clip=clip).split())
        return f"data:image/jpeg;base64,{data}", text
    finally:
        doc.close()
