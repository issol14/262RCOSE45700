"""Small helpers the API modules share."""
from fastapi import HTTPException

from lb import config

DOCS = config.STORAGE / "docs"      # the uploaded PDFs, one file per document id


async def read_json(request):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "요청 형식이 올바르지 않아요.")
    return body


def rect_ok(r):
    """A normalized rect: [x0, y0, x1, y1], each within the page (0..1)."""
    return (isinstance(r, (list, tuple)) and len(r) == 4
            and all(isinstance(v, (int, float)) and -1e-6 <= v <= 1.000001 for v in r))


def union(rects):
    return [min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)]


def id_ok(v):
    return isinstance(v, str) and 0 < len(v) <= 100


def models():
    """The answer models on offer, the vision model last."""
    out = [{"id": m["id"], "label": m["label"], "description": m.get("note", "")} for m in config.CHAT_MODELS]
    if config.VL_MODEL not in [m["id"] for m in out]:
        out.append({"id": config.VL_MODEL, "label": "Qwen3 VL 32B", "description": "문서의 표와 그림 영역 질문"})
    return out
