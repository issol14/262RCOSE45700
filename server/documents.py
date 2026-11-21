"""Documents of a workspace: upload, indexing with a live log, the file, its text, and the
PDF with the learner's margin notes written in (S8)."""
import asyncio
import threading
import time
import unicodedata
from urllib.parse import quote

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from lb import config, export, index, region, store
from lb.parse import text_ratio

from .common import DOCS, id_ok, read_json, rect_ok, union

router = APIRouter(prefix="/api")
_index_lock = threading.Lock()   # one indexing job at a time


def public(d):
    """A stored document in the shape the screens list."""
    p = DOCS / f"{d['id']}.pdf"
    status = {"ready": "ready", "error": "failed"}.get(d["status"], "indexing")
    log = d.get("log") or []
    return {"id": d["id"], "workspaceId": d["space_id"], "name": d["name"], "extension": ".pdf",
            "size": p.stat().st_size if p.exists() else 0, "pages": d["pages_total"] or 0,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(d["created_at"])),
            "chunkCount": index.load(d["id"]).meta.get("chunks", 0) if status == "ready" and index.exists(d["id"]) else 0,
            "indexed": status == "ready", "indexing": status == "indexing", "indexStatus": status,
            # what the indexer is doing right now
            "indexLog": log[-1]["text"] if log else "", "error": d.get("error")}


def get(did):
    d = store.get_doc(did)
    if not d:
        raise HTTPException(404, "문서를 찾을 수 없어요.")
    return d


def remove(did):
    store.delete_doc(did)
    (DOCS / f"{did}.pdf").unlink(missing_ok=True)
    index.remove(did)


def _index_job(did, path):
    def progress(done, total):
        store.update_doc(did, pages_done=done, pages_total=total)
        store.doc_log(did, f"글과 위치 읽기 {done} / {total}쪽", key="read")

    def log(text, key=None):
        store.doc_log(did, text, key=key)
    t0 = time.perf_counter()
    try:
        if _index_lock.locked():
            log("앞 문서 처리가 끝나기를 기다리는 중", key="wait")
        with _index_lock:
            index.build(did, path, on_page=progress, log=log)
        store.doc_log(did, f"준비 완료 · {time.perf_counter() - t0:.1f}초", key="ready", done=True)
        store.update_doc(did, status="ready")
    except Exception as e:  # shown on the document row
        store.doc_log(did, f"실패: {e}", key="error", done=True)
        store.update_doc(did, status="error", error=f"처리 실패: {e}")


@router.get("/documents")
def documents(workspace: str):
    return [public(d) for d in store.list_docs(workspace)]


@router.post("/documents", status_code=201)
async def upload(file: UploadFile = File(...), workspace: str = Form(...)):
    if not id_ok(workspace):
        raise HTTPException(400, "작업 공간을 확인해 주세요.")
    # keep only the file's own name: no client path, one Unicode form (macOS sends NFD Hangul)
    name = unicodedata.normalize("NFC", (file.filename or "문서.pdf").replace("\\", "/").rsplit("/", 1)[-1])[:200] or "문서.pdf"
    if not name.lower().endswith(".pdf"):
        # the rects behind highlights and margin notes are read from PDF pages only
        raise HTTPException(400, f"PDF 파일을 올려 주세요. 최대 {config.MAX_UPLOAD_MB} MB까지 지원해요.")
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"파일이 {config.MAX_UPLOAD_MB} MB를 넘어요.")
    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise HTTPException(400, "PDF 파일을 열 수 없어요.")
    n_pages = pdf.page_count
    readable = text_ratio(pdf) >= 20
    pdf.close()
    if not readable:
        raise HTTPException(400, "텍스트를 읽을 수 없는 문서예요. 스캔한 PDF는 지원하지 않아요.")
    doc = store.create_doc(workspace, name)
    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / f"{doc['id']}.pdf"
    path.write_bytes(data)
    store.update_doc(doc["id"], pages_total=n_pages)
    store.doc_log(doc["id"], f"파일 받음 · {n_pages}쪽 · {len(data) / 1e6:.1f}MB", key="upload")
    threading.Thread(target=_index_job, args=(doc["id"], path), daemon=True).start()
    return public(store.get_doc(doc["id"]))


@router.post("/documents/{did}/index")
async def reindex(did: str):
    d = get(did)
    if d["status"] == "processing":
        raise HTTPException(409, "이미 이 문서의 검색 준비를 진행 중이에요.")
    store.update_doc(did, status="processing", error=None)
    await asyncio.to_thread(_index_job, did, DOCS / f"{did}.pdf")
    d = store.get_doc(did)
    if d["status"] != "ready":
        raise HTTPException(502, d.get("error") or "문서 검색 준비에 실패했어요.")
    return public(d)


@router.get("/documents/{did}/file")
def document_file(did: str):
    get(did)
    p = DOCS / f"{did}.pdf"
    if not p.exists():
        raise HTTPException(404, "문서를 찾을 수 없어요.")
    return FileResponse(p, media_type="application/pdf", headers={"X-Content-Type-Options": "nosniff"})


@router.get("/documents/{did}/text")
def document_text(did: str):
    d = get(did)
    if d["status"] != "ready":
        raise HTTPException(409, "문서 검색 준비가 끝나면 텍스트를 볼 수 있어요.")
    return [{"text": c["text"], "page": c["page"]} for c in index.load(did).chunks]


@router.delete("/documents/{did}")
def delete_document(did: str):
    d = get(did)
    if d["status"] == "processing":
        raise HTTPException(409, "검색 준비가 끝난 뒤 삭제해 주세요.")
    remove(did)
    return {"id": did}


# ---- the PDF with margin notes written in

def _valid_notes(notes, pages):
    def ok(n):
        return (isinstance(n, dict) and isinstance(n.get("page"), int) and not isinstance(n.get("page"), bool)
                and 1 <= n["page"] <= pages
                and all(isinstance(t, str) and len(t) <= 30000 for t in (n.get("excerpt") or "", n.get("question"), n.get("answer")))
                and n["question"].strip() and n["answer"].strip())
    return isinstance(notes, list) and 0 < len(notes) <= 200 and all(ok(n) for n in notes)


def _note_rect(pdf, n):
    """Where a note sits on its page, best evidence first: the cited rects, the dragged region,
    then the excerpt found again in the page text. None when nothing places it."""
    rects = [r for r in (n.get("rects") or []) if rect_ok(r)]
    if rects:
        return union(rects)
    if region.valid(n.get("region")):
        return region.rect_of(n["region"])
    excerpt = " ".join((n.get("excerpt") or "").split())
    if len(excerpt) >= 6:
        page = pdf[n["page"] - 1]
        hits = page.search_for(excerpt[:80]) or page.search_for(excerpt[:24])
        if hits:
            w, h = page.rect.width, page.rect.height
            return [min(r.x0 for r in hits) / w, min(r.y0 for r in hits) / h,
                    max(r.x1 for r in hits) / w, max(r.y1 for r in hits) / h]
    return None


@router.post("/documents/{did}/annotated-file")
async def annotated_file(did: str, request: Request):
    d = get(did)
    notes = (await read_json(request)).get("notes")
    if not _valid_notes(notes, d["pages_total"] or 0):
        raise HTTPException(400, "PDF에 넣을 필기와 페이지를 확인해 주세요.")
    path = DOCS / f"{did}.pdf"
    pdf = fitz.open(path)
    try:
        placed = [{"page": n["page"], "rect": _note_rect(pdf, n), "quote": (n.get("excerpt") or "").strip(),
                   "status": "understood" if n.get("status") == "understood" else "confused",
                   "items": [{"question": n["question"], "text": n["answer"]}]} for n in notes]
    finally:
        pdf.close()
    stem = d["name"][:-4] if d["name"].lower().endswith(".pdf") else d["name"]
    return Response(export.annotated_pdf(path, placed), media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(stem + '-필기.pdf')}",
        "X-Content-Type-Options": "nosniff"})
