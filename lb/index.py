"""Per-document hybrid index: dense (BGE-M3) + BM25 over Kiwi morphemes."""
import json
import math
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import config, models
from .parse import chunk_pdf

_kiwi = None
KEEP = {"NNG", "NNP", "NNB", "NR", "SL", "SN", "SH", "VV", "VA", "XR", "MAG"}


def tokenize(text: str):
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    return [t.form.lower() for t in _kiwi.tokenize(text) if t.tag in KEEP]


class BM25:
    def __init__(self, docs_tokens, k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.n = len(docs_tokens)
        lens = np.array([len(d) for d in docs_tokens], dtype=np.float32)
        avg = float(lens.mean()) if self.n else 1.0
        self.norm = k1 * (1 - b + b * lens / avg)
        self.postings = {}  # term -> (doc indices, term frequencies)
        for i, d in enumerate(docs_tokens):
            for t, f in Counter(d).items():
                self.postings.setdefault(t, ([], []))
                self.postings[t][0].append(i)
                self.postings[t][1].append(f)
        self.postings = {t: (np.array(ix), np.array(fs, dtype=np.float32)) for t, (ix, fs) in self.postings.items()}
        self.idf = {t: math.log(1 + (self.n - len(ix) + 0.5) / (len(ix) + 0.5)) for t, (ix, _) in self.postings.items()}

    def scores(self, q_tokens):
        s = np.zeros(self.n, dtype=np.float32)
        for t in set(q_tokens):
            if t not in self.postings:
                continue
            ix, f = self.postings[t]
            s[ix] += self.idf[t] * f * (self.k1 + 1) / (f + self.norm[ix])
        return s


class DocIndex:
    def __init__(self, d: Path):
        self.dir = d
        self.chunks = json.loads((d / "chunks.json").read_text())
        self.dense = np.load(d / "dense.npy")
        self.bm25 = BM25(json.loads((d / "tokens.json").read_text()))
        self.meta = json.loads((d / "meta.json").read_text())


def build(doc_id: str, pdf_path, on_page=None, log=None) -> Path:
    """`log(text, key=None)` narrates the stages for the live processing log."""
    log = log or (lambda *a, **k: None)
    d = config.INDEX_DIR / doc_id
    d.mkdir(parents=True, exist_ok=True)
    chunks, n_pages = chunk_pdf(pdf_path, on_page=on_page)
    texts = [c["text"] for c in chunks]
    log(f"문단 {len(chunks)}개로 나누기 · 쪽을 넘지 않게", key="chunk")
    log(f"문단 의미 벡터 만들기 0 / {len(texts)}", key="embed")
    dense = (models.embed(texts, on_batch=lambda n, t: log(f"문단 의미 벡터 만들기 {n} / {t}", key="embed"))
             if texts else np.zeros((0, 1024), np.float32))
    log("낱말 색인 만들기 (형태소 분석)", key="bm25")
    tokens = [tokenize(t) for t in texts]
    (d / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False))
    np.save(d / "dense.npy", dense)
    (d / "tokens.json").write_text(json.dumps(tokens, ensure_ascii=False))
    (d / "meta.json").write_text(json.dumps({"pages": n_pages, "chunks": len(chunks)}))
    load.cache_clear()
    return d


def exists(doc_id: str) -> bool:
    return (config.INDEX_DIR / doc_id / "meta.json").exists()


def remove(doc_id: str) -> None:
    import shutil
    shutil.rmtree(config.INDEX_DIR / doc_id, ignore_errors=True)
    load.cache_clear()


@lru_cache(maxsize=128)
def load(doc_id: str) -> DocIndex:
    return DocIndex(config.INDEX_DIR / doc_id)
