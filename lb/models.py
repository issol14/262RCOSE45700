"""Embedding and reranking models, loaded once per process."""
import functools
import numpy as np

from . import config


@functools.lru_cache(maxsize=1)
def embedder():
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(config.EMBED_MODEL, device=config.DEVICE,
                            model_kwargs={"torch_dtype": "float16"})
    m.max_seq_length = 1024
    return m


@functools.lru_cache(maxsize=1)
def reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(config.RERANK_MODEL, device=config.DEVICE, max_length=1024,
                        model_kwargs={"torch_dtype": "float16"})


def _post(url, body):
    import requests
    r = requests.post(url, json=body, timeout=120)
    r.raise_for_status()
    return r.json()


def embed(texts, batch_size=32, on_batch=None) -> np.ndarray:
    """`on_batch(done, total)` reports progress (remote path, batch by batch)."""
    if config.EMBED_URL:
        out = []
        for i in range(0, len(texts), batch_size):
            data = _post(config.EMBED_URL, {"model": config.REMOTE_EMBED_MODEL, "input": texts[i:i + batch_size]})["data"]
            out += [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
            if on_batch:
                on_batch(len(out), len(texts))
        v = np.asarray(out, dtype=np.float32).reshape(len(texts), -1)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
    v = embedder().encode(texts, batch_size=batch_size, normalize_embeddings=True,
                          convert_to_numpy=True, show_progress_bar=False)
    if on_batch:
        on_batch(len(texts), len(texts))
    return v.astype(np.float32)


def rerank(query, passages, batch_size=32) -> np.ndarray:
    """Relevance in 0..1 (sigmoid of the cross-encoder logit)."""
    if not passages:
        return np.zeros(0, dtype=np.float32)
    if config.RERANK_URL:
        res = _post(config.RERANK_URL, {"model": config.REMOTE_RERANK_MODEL, "query": query, "documents": passages})
        s = np.zeros(len(passages), dtype=np.float32)
        for x in res["results"]:
            s[x["index"]] = x["relevance_score"]
        return s
    import torch
    logits = reranker().predict([(query, p) for p in passages], batch_size=batch_size,
                                show_progress_bar=False, activation_fn=torch.nn.Identity())
    return 1 / (1 + np.exp(-np.asarray(logits, dtype=np.float32)))
