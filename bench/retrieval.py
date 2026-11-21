"""Retrieval-only diagnostic: is the gold page among the top-k passages?"""
import json
import sys

from lb import retrieve
from bench.sets import load

name = sys.argv[1] if len(sys.argv) > 1 else "dev"
items = load(name)
res = {}
for mode in retrieve.MODES:
    hits = {1: 0, 3: 0, 5: 0, 10: 0}
    for it in items:
        ps = retrieve.retrieve(it["doc_id"], it["question"], mode=mode, top_k=10)
        pages = [p["chunk"]["page"] for p in ps]
        for k in hits:
            hits[k] += any(p in it["pages"] for p in pages[:k])
    res[mode] = {f"recall@{k}": round(v / len(items), 3) for k, v in hits.items()}
    print(mode, res[mode], flush=True)
print(json.dumps(res))
