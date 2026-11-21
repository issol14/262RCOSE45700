"""Test setup: a scratch store, and small stand-ins for the three model calls.

Nothing here reaches an endpoint. Embedding is a bag of character bigrams, the reranker is
bigram overlap, and the generator is whatever a test scripts with `llm.reply`.
"""
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="lb-test-"))
os.environ.update(LB_STORAGE=str(_TMP / "app"), LB_INDEX_DIR=str(_TMP / "app" / "index"),
                  LB_LLM_BASE_URL="http://127.0.0.1:9/v1", LB_LLM_API_KEY="test",
                  LB_LLM_MODEL="furiosa-ai/gemma-4-31B-it-FP8-block")
os.environ.pop("LB_EMBED_URL", None)
os.environ.pop("LB_RERANK_URL", None)

import fitz  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from lb import generate, models  # noqa: E402

DIM = 256


def _grams(text):
    t = "".join(text.lower().split())
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def fake_embed(texts, batch_size=32, on_batch=None):
    v = np.zeros((len(texts), DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        for g in _grams(t):
            v[i, hash(g) % DIM] += 1
    if on_batch:
        on_batch(len(texts), len(texts))
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


def fake_rerank(query, passages, batch_size=32):
    q = _grams(query)
    return np.asarray([len(q & _grams(p)) / max(len(q), 1) for p in passages], dtype=np.float32)


class FakeLLM:
    """Records every call; replies with `reply` (a string, or a function of the messages)."""

    def __init__(self):
        self.calls = []
        self.reply = "미확인"

    async def chat(self, messages, model=None, temperature=0.7, top_p=0.8, max_tokens=700, seed=None, on_token=None):
        self.calls.append({"messages": messages, "model": model})
        text = self.reply(messages) if callable(self.reply) else self.reply
        if on_token:
            for piece in (text[: len(text) // 2], text[len(text) // 2:]):
                await on_token(piece)
        return text, 0.01, 0.02


# Indexing runs on a background thread that outlives a test's monkeypatch, so the two
# retrieval models are replaced for the whole session; only the generator is per test.
models.embed = fake_embed
models.rerank = fake_rerank


@pytest.fixture(autouse=True)
def llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(generate, "chat", fake.chat)
    return fake


PAGES = [
    "리랭킹은 검색한 후보 문단을 질문과 다시 맞춰 순위를 매기는 단계다.\n\n크로스 인코더는 질문과 문단을 함께 읽어 점수를 낸다.",
    "TTFT는 첫 토큰이 나올 때까지 걸린 시간이다.\n\n검색 문맥이 길어지면 TTFT도 길어진다.",
    "배치 크기를 키우면 처리량이 늘지만 지연 시간도 함께 늘어난다.",
]


def make_pdf(path, pages=PAGES):
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        y = 80
        for para in text.split("\n\n"):
            page.insert_textbox(fitz.Rect(60, y, 540, y + 60), para, fontname="korea", fontsize=12)
            y += 90
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def pdf(tmp_path):
    return make_pdf(tmp_path / "lecture.pdf")
