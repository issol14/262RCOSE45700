"""Run the pipeline (or the closed-book baseline) over a benchmark set.

Answers are produced with the abstain gate off (tau=-1) and the top reranker
score is stored, so the threshold can be chosen afterwards without rerunning.

usage: python -m bench.run --set dev --tag 14b-hr [--mode hybrid_rerank] [--rewrite]
                           [--baseline] [--repeats 1] [--concurrency 16]
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

from lb import config, generate, pipeline, retrieve
from bench.sets import load

ap = argparse.ArgumentParser()
ap.add_argument("--set", required=True)
ap.add_argument("--tag", required=True)
ap.add_argument("--mode", default="hybrid_rerank", choices=retrieve.MODES)
ap.add_argument("--rewrite", action="store_true")
ap.add_argument("--baseline", action="store_true")
ap.add_argument("--repeats", type=int, default=1)
ap.add_argument("--concurrency", type=int, default=16)
ap.add_argument("--model", default=None)
ap.add_argument("--retrieve-only", action="store_true", help="fill the passage cache and exit")
ap.add_argument("--skip-gated", action="store_true",
                help="do not call the generator when the reranker gate (tau) abstains anyway; metrics are identical")
args = ap.parse_args()

OUT = config.ROOT / "results" / args.tag
OUT.mkdir(parents=True, exist_ok=True)


def _gated(ps):
    top = max((p["rerank"] for p in ps if p.get("rerank") is not None), default=None)
    return not ps or (top is not None and top < config.ABSTAIN_TAU)


async def main():
    items = load(args.set)
    model = None if args.retrieve_only else (args.model or (await generate.client().models.list()).data[0].id)
    sem = asyncio.Semaphore(args.concurrency)
    passages = rewrites = None
    if not args.baseline:
        rewrites = [None] * len(items)
        if args.rewrite:
            async def rw(it):
                async with sem:
                    return await generate.rewrite(it["question"], model=model)
            rewrites = await asyncio.gather(*(rw(it) for it in items))
        # retrieval does not depend on the generator, so it is cached and the GPU
        # can be given entirely to the LLM when comparing models
        cache = config.ROOT / "results" / "_passages" / f"{args.set}_{args.mode}.json"
        if not args.rewrite and cache.exists():
            passages = json.loads(cache.read_text())
        else:
            t = time.time()
            passages = [retrieve.retrieve(it["doc_id"], [it["question"]] + ([rewrites[i]] if rewrites[i] else []),
                                          mode=args.mode, rerank_query=it["question"])
                        for i, it in enumerate(items)]
            print(f"retrieval {time.time()-t:.1f}s ({len(items)} q)", flush=True)
            if not args.rewrite:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(passages, ensure_ascii=False))
    if args.retrieve_only:
        return

    for r in range(args.repeats):
        async def one(i, it):
            async with sem:
                if args.baseline:
                    res = await pipeline.closed_book(it["question"], model=model, seed=1000 * r + i)
                elif args.skip_gated and _gated(passages[i]):
                    res = await pipeline.answer(it["doc_id"], it["question"], mode=args.mode, passages=passages[i])
                else:
                    res = await pipeline.answer(it["doc_id"], it["question"], mode=args.mode, tau=-1.0,
                                                model=model, seed=1000 * r + i, passages=passages[i])
                    res["rewritten"] = rewrites[i]
                for c in res.get("citations", []):
                    c.pop("rects", None)
                return {**{k: it[k] for k in ("qid", "domain", "question", "answer", "doc_id", "pages",
                                               "type", "answerable", "scanned")}, **res, "model": model, "run": r}
        t = time.time()
        rows = await asyncio.gather(*(one(i, it) for i, it in enumerate(items)))
        wall = time.time() - t
        f = OUT / f"{args.set}_r{r}.jsonl"
        f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n")
        print(f"{f} n={len(rows)} wall={wall:.1f}s", flush=True)
    (OUT / "config.json").write_text(json.dumps({**vars(args), "model": model}, ensure_ascii=False, indent=1))


asyncio.run(main())
