"""LearnBoost web server: the API under /api and the built screens (ui/dist)."""
import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lb import config

from . import chat, concepts, documents, records
from .common import models


@asynccontextmanager
async def lifespan(_app):
    # reach the embedding and reranking models once so the first question is not slow
    from lb import models as retrieval_models
    try:
        retrieval_models.embed(["warmup"])
        retrieval_models.rerank("warmup", ["warmup"])
    except Exception as e:  # keep serving; the first question will retry and report
        print(f"[warmup] embedding/rerank not ready: {e}", flush=True)
    yield


app = FastAPI(title="LearnBoost", lifespan=lifespan)
DIST = config.ROOT / "ui" / "dist"
# extra origins allowed to call the API (comma-separated hosts), e.g. the Vite dev server
ALLOWED_ORIGINS = {h.strip() for h in os.environ.get("LB_ALLOWED_ORIGINS", "").split(",") if h.strip()}


@app.middleware("http")
async def same_origin_api(request: Request, call_next):
    """The API answers its own page only, and is never cached: a page on another site must not
    be able to read a learner's documents or spend the endpoint through their browser."""
    if request.url.path.startswith("/api"):
        origin = request.headers.get("origin")
        if origin:
            host = urlsplit(origin).netloc
            if host != request.headers.get("host") and host not in ALLOWED_ORIGINS:
                return JSONResponse({"error": "다른 사이트에서 보낸 요청은 받지 않아요."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        return response
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.get("/api/config")
def api_config():
    """What the screens need to know about the server: the models on offer and whether a key
    is set (never the key)."""
    return {"model": config.LLM_MODEL, "models": models(), "llmHost": urlsplit(config.LLM_BASE_URL).netloc,
            # a model server on this machine needs no key
            "apiKeyConfigured": config.LLM_API_KEY not in ("", "local")
            or urlsplit(config.LLM_BASE_URL).hostname in ("127.0.0.1", "localhost"),
            "embeddingModel": config.REMOTE_EMBED_MODEL if config.EMBED_URL else config.EMBED_MODEL,
            "rerankModel": config.REMOTE_RERANK_MODEL if config.RERANK_URL else config.RERANK_MODEL,
            "visionModel": config.VL_MODEL, "maxUploadMb": config.MAX_UPLOAD_MB}


@app.get("/api/health")
def api_health(model: str | None = None):
    """Connection check: asks the generation server for its model list. No generation call."""
    model = model or config.LLM_MODEL
    ids = [m["id"] for m in models()]
    if model not in ids:
        raise HTTPException(400, "지원하는 답변 모델을 선택해 주세요.")
    host = urlsplit(config.LLM_BASE_URL).netloc
    try:
        r = requests.get(f"{config.LLM_BASE_URL}/models", timeout=10,
                         headers={"Authorization": f"Bearer {config.LLM_API_KEY}"})
        if r.status_code != 200:
            return {"available": False, "model": model,
                    "message": f"{host}이(가) HTTP {r.status_code}로 답했어요. 서버의 API 키와 주소를 확인해 주세요."}
        offered = {m.get("id") for m in r.json().get("data", [])}
    except Exception as e:
        return {"available": False, "model": model, "message": f"{host}에 연결할 수 없어요. ({type(e).__name__})"}
    ok = model in offered
    return {"available": ok, "model": model, "models": [{"id": i, "available": i in offered} for i in ids],
            "message": "생성 서버에 연결됐어요. 답변을 생성할 수 있어요." if ok else f"이 서버에서 {model} 모델을 찾을 수 없어요."}


for module in (documents, chat, concepts, records):
    app.include_router(module.router)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def api_not_found(path: str):
    raise HTTPException(404, "요청한 API 경로를 찾을 수 없어요.")


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = (DIST / path).resolve()
        if path and f.is_relative_to(DIST.resolve()) and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
