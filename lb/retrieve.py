"""Hybrid retrieval: dense + BM25 → RRF → rerank, inside one document or across a space."""
import numpy as np

from . import config, index, models

MODES = ("dense", "hybrid", "hybrid_rerank")


def _rrf(rank_lists, k=60):
    score = {}
    for ranks in rank_lists:
        for r, i in enumerate(ranks):
            score[i] = score.get(i, 0.0) + 1.0 / (k + r + 1)
    return sorted(score, key=score.get, reverse=True)


def _candidates(idx, queries, qv, mode):
    lists = []
    for q, v in zip(queries, qv):
        dense = idx.dense @ v
        lists.append(list(np.argsort(-dense)[: config.TOP_DENSE]))
        if mode != "dense":
            bm = idx.bm25.scores(index.tokenize(q))
            lists.append([i for i in np.argsort(-bm)[: config.TOP_BM25] if bm[i] > 0])
    return _rrf(lists)


def retrieve(doc_id, queries, mode="hybrid_rerank", rerank_query=None, top_k=None, on_pool=None):
    """Return top passages [{chunk, rerank}] for one or more query strings.

    `queries` may hold the original question plus rewrites; all candidate lists
    are fused with RRF. The reranker always scores against `rerank_query`
    (the user's own question), so a rewrite cannot shift the abstain gate.
    `on_pool(n)` reports how many candidates go to the reranker (progress log).
    """
    top_k = top_k or config.TOP_CONTEXT
    idx = index.load(doc_id)
    if not idx.chunks:
        return []
    if isinstance(queries, str):
        queries = [queries]
    qv = models.embed(queries)
    cand = _candidates(idx, queries, qv, mode)
    if mode != "hybrid_rerank":
        return [{"chunk": idx.chunks[i], "rerank": None} for i in cand[:top_k]]
    cand = cand[: config.TOP_RERANK_IN]
    if on_pool:
        on_pool(len(cand))
    rq = rerank_query or queries[0]
    s = models.rerank(rq, [idx.chunks[i]["text"] for i in cand])
    order = np.argsort(-s)[:top_k]
    return [{"chunk": idx.chunks[cand[j]], "rerank": float(s[j])} for j in order]


def retrieve_many(doc_ids, queries, rerank_query=None, top_k=None, on_pool=None):
    """Space-wide retrieval: every document's candidates, reranked together.

    One document goes through `retrieve` unchanged. With several, each document keeps
    its own TOP_RERANK_IN candidates (the pool the single-document gate was tuned on),
    interleaved up to MAX_RERANK_POOL, and one reranker call orders them all.
    Passages carry `doc_id`.
    """
    if len(doc_ids) == 1:
        return [{**p, "doc_id": doc_ids[0]}
                for p in retrieve(doc_ids[0], queries, rerank_query=rerank_query, top_k=top_k, on_pool=on_pool)]
    top_k = top_k or config.TOP_CONTEXT
    if isinstance(queries, str):
        queries = [queries]
    qv = models.embed(queries)
    per_doc = []
    for d in doc_ids:
        idx = index.load(d)
        if idx.chunks:
            per_doc.append((d, idx, _candidates(idx, queries, qv, "hybrid_rerank")[: config.TOP_RERANK_IN]))
    pool = []
    for r in range(config.TOP_RERANK_IN):
        for d, idx, c in per_doc:
            if r < len(c):
                pool.append((d, idx.chunks[c[r]]))
    pool = pool[: config.MAX_RERANK_POOL]
    if not pool:
        return []
    if on_pool:
        on_pool(len(pool))
    s = models.rerank(rerank_query or queries[0], [ch["text"] for _, ch in pool])
    order = np.argsort(-s)[:top_k]
    return [{"chunk": pool[j][1], "rerank": float(s[j]), "doc_id": pool[j][0]} for j in order]
