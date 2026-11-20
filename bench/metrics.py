"""Metrics from judged runs.

A gold-page match is exact (physical PDF page). The abstain threshold tau is
applied after the fact: an answer counts as abstained if the model said
"미확인" or the top reranker score was below tau.
"""
import json
import statistics as st
from pathlib import Path


def read(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def effective(r, tau):
    gated = r.get("top_score") is not None and r["top_score"] < tau
    return r["abstained"] or gated


def pos_metrics(rows, tau):
    n = len(rows)
    acc = support = page_hit = over = 0
    ans = cites = cite_ok = 0
    for r in rows:
        if effective(r, tau):
            over += 1
            continue
        ans += 1
        acc += r.get("j_correct", {}).get("score", 0)
        cs = r.get("citations") or []
        support += r.get("j_support", {}).get("score", 0) if cs else 0
        pages = {c["page"] for c in cs}
        page_hit += bool(pages & set(r["pages"]))
        cites += len(cs)
        cite_ok += sum(c["page"] in r["pages"] for c in cs)
    lat = [r["total_s"] for r in rows if r.get("total_s") and not effective(r, tau)]
    ttft = [r["ttft_s"] for r in rows if r.get("ttft_s") and not effective(r, tau)]
    return {
        "n": n,
        "accuracy": acc / n,                                   # S1 정답률 (abstained = wrong)
        "citation_support": support / ans if ans else 0.0,     # S1 인용 근거 일치율 (B, judge)
        "citation_page_precision": cite_ok / cites if cites else 0.0,  # (A) cited page is gold page
        "page_hit": page_hit / n,                              # S2 근거 페이지 일치율
        "over_abstain": over / n,                              # S3 과잉 보류율
        "answered": ans / n,
        "latency_median_s": st.median(lat) if lat else None,
        "ttft_median_s": st.median(ttft) if ttft else None,
    }


def neg_metrics(rows, tau):
    return {"n": len(rows), "abstain_rate": sum(effective(r, tau) for r in rows) / len(rows)}  # S3 보류율


def mean_sd(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {"mean": round(st.mean(vals), 3), "sd": round(st.pstdev(vals), 3) if len(vals) > 1 else 0.0}


def summarize(tag_dir, set_name, tau, neg_name=None, text_only=False):
    """text_only drops items whose source PDF is scanned (the product rejects those)."""
    d = Path(tag_dir)
    keep = (lambda r: not r.get("scanned")) if text_only else (lambda r: True)
    runs = sorted(d.glob(f"{set_name}_r*.judged.jsonl"))
    per = [pos_metrics([r for r in read(p) if keep(r)], tau) for p in runs]
    out = {"runs": len(per), "tau": tau, "text_only": text_only}
    for k in per[0]:
        out[k] = mean_sd([m[k] for m in per]) if k != "n" else per[0]["n"]
    if neg_name:
        nruns = sorted(d.glob(f"{neg_name}_r*.jsonl"))
        nruns = [p for p in nruns if not p.name.endswith(".judged.jsonl")] or nruns
        negs = [neg_metrics([r for r in read(p) if keep(r)], tau) for p in nruns]
        out["abstain_rate"] = mean_sd([m["abstain_rate"] for m in negs])
        out["neg_runs"] = len(negs)
    return out


if __name__ == "__main__":
    import sys
    tag, s = sys.argv[1], sys.argv[2]
    tau = float(sys.argv[3]) if len(sys.argv) > 3 else -1
    neg = sys.argv[4] if len(sys.argv) > 4 else None
    print(json.dumps(summarize(tag, s, tau, neg, text_only="--text-only" in sys.argv), ensure_ascii=False, indent=1))
