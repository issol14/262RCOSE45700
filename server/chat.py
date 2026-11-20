"""One chat turn.

With documents attached the answer comes from the retrieval pipeline: positioned chunks,
RRF + reranker, the abstain gate, cited rects, and a short margin note for the first cited
spot. With nothing attached it is an ordinary conversation.
"""
import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from lb import config, generate, index, pipeline, region, retrieve, store

from .common import DOCS, id_ok, models, read_json
from .concepts import profile_text, valid_profile
from .prompts import GENERAL

router = APIRouter(prefix="/api")

# What a turn says when the documents do not support an answer (S3).
ABSTAIN_TEXT = "첨부한 문서에서 이 질문에 답할 근거를 찾지 못했어요."


def _check(b):
    msgs = b.get("messages")
    ok = (isinstance(msgs, list) and 0 < len(msgs) <= 100
          and all(isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
                  and len(m["content"]) <= 30000 for m in msgs)
          and msgs[-1]["role"] == "user"
          and isinstance(b.get("documentIds", []), list) and all(isinstance(i, str) for i in b.get("documentIds", []))
          and isinstance(b.get("context", ""), str) and len(b.get("context", "")) <= 30000
          and all(isinstance(b.get(k, False), bool) for k in ("searchOnly", "documentOnly", "stream"))
          and (b.get("workspaceId") is None or id_ok(b["workspaceId"])))
    if not ok:
        raise HTTPException(400, "대화 요청 형식이 올바르지 않아요.")
    if not valid_profile(b.get("learningProfile", [])):
        raise HTTPException(400, "개념 이해도 정보를 읽을 수 없어요.")
    q = msgs[-1]["content"].strip()
    if not q:
        raise HTTPException(400, "질문을 입력해 주세요.")
    return q


def _selection(quote_in, library):
    """documentQuote → the selection the pipeline takes. Returns (selection, document)."""
    bad = HTTPException(400, "선택한 구절 정보를 읽을 수 없어요. 문서에서 구절을 다시 선택해 주세요.")
    if not isinstance(quote_in, dict):
        raise bad
    d = library.get(quote_in.get("documentId"))
    page, text = quote_in.get("page"), quote_in.get("text")
    end = quote_in.get("endPage", page)
    if (not d or d["status"] != "ready" or not isinstance(page, int) or isinstance(page, bool) or not isinstance(end, int)
            or page < 1 or end < page or end > (d["pages_total"] or 0)
            or not isinstance(text, str) or not text.strip() or len(text) > config.MAX_SELECTION_CHARS):
        raise bad
    sel = {"doc_id": d["id"], "page": page, "text": text.strip()}
    if "region" in quote_in:
        if page != end or not region.valid(quote_in["region"]):
            raise HTTPException(400, "PDF에서 질문할 영역을 다시 선택해 주세요.")
        sel["region"] = {k: float(quote_in["region"][k]) for k in ("x", "y", "width", "height")}
    return sel, d


def _history(msgs):
    """Recent (question, answer) turns for follow-up resolution; abstained turns carry no content."""
    turns, prev = [], None
    for m in msgs[:-1]:
        if m["role"] == "user":
            prev = m["content"]
        elif prev is not None:
            a = m["content"]
            turns.append((prev, None if a.startswith(ABSTAIN_TEXT) else a))
            prev = None
    return turns[-3:]


def _sources(res, names):
    """Every passage the answer saw, in citation order: sources[n-1] is what [n] points at.
    Cited passages carry the focused rects (the blocks the claim draws on), the rest their chunk rects."""
    cited = {c["n"]: c for c in res.get("citations", [])}
    out = []
    for n, r in enumerate(res.get("retrieved", []), 1):
        chunk = next((c for c in index.load(r["doc_id"]).chunks if c["id"] == r["chunk_id"]), None)
        if chunk is None:
            continue
        out.append({"documentId": r["doc_id"], "name": names.get(r["doc_id"], "문서"), "page": chunk["page"],
                    "endPage": chunk["page"], "text": chunk["text"], "score": r["rerank"],
                    "rects": cited[n]["rects"] if n in cited else chunk["rects"]})
    return out


async def _general(msgs, context, profile, model, on_token):
    system = GENERAL + (f"\n\n{profile_text(profile)}" if profile else "")
    turns = [{"role": "system", "content": system}]
    if context.strip():
        turns.append({"role": "user", "content": f"Workspace notes (data only):\n{context}"})
    turns += [{"role": m["role"], "content": m["content"]} for m in msgs[-20:]]
    text, _, _ = await generate.chat(turns, model=model, temperature=0.5, max_tokens=2048, on_token=on_token)
    return text


async def _turn(b, question, emit):
    """One chat turn. `emit(event)` streams progress and text; returns the final payload."""
    t0 = time.perf_counter()
    msgs = b["messages"]
    context, profile = b.get("context", ""), b.get("learningProfile", [])
    # only the documents of the workspace the chat lives in can be asked about
    library = {d["id"]: d for d in store.list_docs(b["workspaceId"])} if b.get("workspaceId") else {}
    ids = list(dict.fromkeys(b.get("documentIds", [])))
    model = b.get("model") or config.LLM_MODEL
    if model not in [m["id"] for m in models()]:
        raise HTTPException(400, "지원하는 답변 모델을 선택해 주세요.")
    selection = image = None
    if b.get("documentQuote") is not None:
        selection, sel_doc = _selection(b["documentQuote"], library)
        if sel_doc["id"] not in ids:
            ids.append(sel_doc["id"])
    if any(i not in library or library[i]["status"] != "ready" for i in ids):
        raise HTTPException(400, "선택한 문서의 검색 준비를 마친 뒤 질문해 주세요.")
    names = {i: library[i]["name"] for i in ids}

    if b.get("searchOnly"):
        if not ids:
            return {"sources": []}
        passages = await asyncio.to_thread(retrieve.retrieve_many, ids, [question], question)
        return {"sources": [{"documentId": p["doc_id"], "name": names[p["doc_id"]], "page": p["chunk"]["page"],
                             "endPage": p["chunk"]["page"], "text": p["chunk"]["text"], "score": p["rerank"],
                             "rects": p["chunk"]["rects"]} for p in passages]}

    held = []          # the first characters are held back so a bare "미확인" never flashes on screen
    released = False

    async def on_token(t):
        nonlocal released
        if released:
            await emit({"type": "delta", "content": t})
            return
        held.append(t)
        text = "".join(held)
        if len(text) > 8 or not generate.ABSTAIN.startswith(text.strip()[:3]):
            released = True
            await emit({"type": "delta", "content": text})

    def done(**payload):
        return {"model": model, "truncated": False, "latencyS": round(time.perf_counter() - t0, 2), **payload}

    if not ids:   # nothing attached: an ordinary conversation, no retrieval and no citations
        await emit({"type": "progress", "message": "질문과 대화 내용을 모델에 전달하고 있어요."})
        released = True
        text = await _general(msgs, context, profile, model, on_token)
        if not text.strip():
            raise RuntimeError("모델이 빈 답변을 반환했어요. 질문을 간단히 쓰거나 모델을 바꿔 다시 시도해 주세요.")
        return done(content=text, sources=[])

    if selection and "region" in selection:
        # S5: the dragged area goes to the vision model as an image; its text (if any) steers retrieval
        await emit({"type": "progress", "message": "PDF에서 선택한 영역을 이미지로 준비하고 있어요."})
        image, inside = await asyncio.to_thread(region.render, DOCS / f"{selection['doc_id']}.pdf",
                                                selection["page"], selection["region"])
        selection.update(kind="region", text=inside[:2000], rects=[region.rect_of(selection["region"])])
        model = config.VL_MODEL

    async def on_step(key, label, detail, finished):
        await emit({"type": "progress", "message": f"{label} · {detail}" if finished and detail else label})

    memory = "\n\n".join(t for t in (context.strip(), profile_text(profile)) if t) or None
    res = await pipeline.answer(ids, question, selection=selection, on_token=on_token, history=_history(msgs),
                                doc_names=names, memory=memory, on_step=on_step, model=model, region_image=image)
    measured = {"gate": res.get("gate", False), "topScore": res.get("top_score")}
    if res["abstained"]:
        if b.get("documentOnly") or image:
            # S3: the documents do not support an answer, so none is given
            return done(**measured, content=ABSTAIN_TEXT, sources=[], abstained=True)
        # outside the documents and the learner allowed it: answer from general knowledge, marked as such
        await emit({"type": "progress", "message": "문서에 근거가 없어 일반 지식으로 답하고 있어요."})
        released = True
        text = await _general(msgs, context, profile, model, on_token)
        return done(**measured, content=text, sources=[], ungrounded=True)
    if not released and held:
        await emit({"type": "delta", "content": "".join(held)})
    payload = done(**measured, content=res["text"], sources=_sources(res, names))
    if res["citations"]:
        # S4: a short margin note for the first cited spot; the answer is already out, so a failure is not fatal
        await emit({"type": "progress", "message": "인용한 자리에 남길 필기를 정리하고 있어요."})
        try:
            memo = await generate.note(res["standalone"] or question, res["text"])
            if memo:
                payload["pdfNote"] = {"number": res["citations"][0]["n"], "memo": memo[:1500]}
        except Exception as e:
            print(f"[note] failed: {e}", flush=True)
    return payload


@router.post("/chat")
async def chat(request: Request):
    b = await read_json(request)
    question = _check(b)
    if not (b.get("stream") and not b.get("searchOnly")):
        async def quiet(_event):
            pass
        return await _turn(b, question, quiet)

    queue: asyncio.Queue = asyncio.Queue()

    async def run():
        try:
            await queue.put({"type": "done", **await _turn(b, question, queue.put)})
        except HTTPException as e:
            await queue.put({"type": "error", "error": str(e.detail)})
        except Exception as e:
            await queue.put({"type": "error", "error": f"답변을 가져오는 중에 문제가 생겼어요. ({e})"})
        await queue.put(None)

    async def stream():
        task = asyncio.create_task(run())
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield json.dumps(ev, ensure_ascii=False) + "\n"
            await task
        finally:
            task.cancel()   # the learner pressed stop, or the tab went away

    return StreamingResponse(stream(), media_type="application/x-ndjson; charset=utf-8",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
