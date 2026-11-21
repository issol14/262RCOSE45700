"""Benchmark sets from Allganize RAG-Evaluation-Dataset-KO.

eval:    paragraph questions whose source PDF we have (128)
dev:     text questions whose source PDF we have (29); documents never overlap eval
*_neg:   the same questions asked against a document of another domain (abstain sets)
s5:      10 table/image questions (Later story S5, optional)

Items whose source PDF is scanned (no text layer) keep `scanned=True`; metrics
are reported on all items and on the text-PDF subset.
"""
import csv
import json
import random
import unicodedata

from lb import config

D = config.DATA / "allganize"


def nfc(s):
    return unicodedata.normalize("NFC", s)


def pdf_map():
    return json.loads((D / "pdf_map.json").read_text())


def pdf_path(doc_id):
    return D / "pdf" / f"{doc_id}.pdf"


def rows():
    m = pdf_map()
    out = []
    for i, r in enumerate(csv.DictReader(open(D / "rag_evaluation_result.csv", encoding="utf-8"))):
        name = nfc(r["target_file_name"])
        if name not in m:
            continue
        pages = [int(p) for p in str(r["target_page_no"]).replace(" ", "").split(",") if p.strip().isdigit()]
        out.append({"qid": i, "domain": r["domain"], "question": r["question"].strip(),
                    "answer": r["target_answer"].strip(), "doc_id": m[name], "doc_name": name,
                    "pages": pages, "type": r["context_type"]})
    return out


def scanned_docs():
    """Documents with no extractable text (scanned). The product rejects these (OCR is Out)."""
    out = set()
    for d in doc_ids():
        meta = config.INDEX_DIR / d / "meta.json"
        if meta.exists() and json.loads(meta.read_text())["chunks"] == 0:
            out.add(d)
    return out


def _negatives(items, all_rows, seed):
    rng = random.Random(seed)
    scan = scanned_docs()
    by_dom = {}
    for r in all_rows:
        if r["doc_id"] not in scan:   # an empty document would make abstaining trivial
            by_dom.setdefault(r["domain"], set()).add(r["doc_id"])
    neg = []
    for it in items:
        pool = sorted(d for dom, ds in by_dom.items() if dom != it["domain"] for d in ds)
        neg.append({**it, "doc_id": rng.choice(pool), "answerable": False})
    return neg


def load(name):
    rs = rows()
    sets = {
        "eval": [r for r in rs if r["type"] == "paragraph"],
        "dev": [r for r in rs if r["type"] == "text"],
    }
    sets["eval_neg"] = _negatives(sets["eval"], rs, seed=7)
    sets["dev_neg"] = _negatives(sets["dev"], rs, seed=11)
    rng = random.Random(5)
    ti = [r for r in rs if r["type"] in ("table", "image")]
    sets["s5"] = rng.sample(ti, 10)
    scan = scanned_docs()
    for k in ("eval", "dev", "s5"):
        for r in sets[k]:
            r["answerable"] = True
    for v in sets.values():
        for r in v:
            r["scanned"] = r["doc_id"] in scan
    return sets[name]


def doc_ids():
    return sorted(set(pdf_map().values()))
