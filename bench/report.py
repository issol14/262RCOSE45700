"""Write results/REPORT.md, results/summary.json and results/human_check.csv from judged runs."""
import csv
import json
import random
from collections import Counter
from pathlib import Path

from lb import config
from bench.metrics import read, summarize

R = config.ROOT / "results"
TAU = config.ABSTAIN_TAU
TARGET = {"accuracy": (0.75, ">="), "citation_support": (0.85, ">="), "page_hit": (0.80, ">="),
          "abstain_rate": (0.80, ">="), "over_abstain": (0.15, "<=")}
LABEL = {"accuracy": "S1 정답률", "citation_support": "S1 인용 근거 일치율 (판정기)",
         "citation_page_precision": "참고: 인용 문단 쪽 = 정답 쪽 비율", "page_hit": "S2 근거 페이지 일치율",
         "abstain_rate": "S3 보류율", "over_abstain": "S3 과잉 보류율",
         "latency_median_s": "응답 시간 중앙값 (초, 동시 16)", "ttft_median_s": "첫 글자 시간 중앙값 (초, 동시 16)"}


def fmt(v):
    return "–" if v is None else f"{v['mean']:.3f} ± {v['sd']:.3f}"


def mark(k, v):
    if k not in TARGET or v is None:
        return ""
    t, op = TARGET[k]
    ok = v["mean"] >= t if op == ">=" else v["mean"] <= t
    return f"{op} {t} {'달성' if ok else '미달'}"


def failure_breakdown(tag):
    c = Counter()
    for p in sorted((R / tag).glob("eval_r*.judged.jsonl")):
        for r in read(p):
            if r.get("scanned"):
                c["스캔 PDF(텍스트 없음, 범위 밖)"] += 1
                continue
            gated = r.get("top_score") is not None and r["top_score"] < TAU
            if r["abstained"] or gated:
                c["보류 오판(답이 있는데 미확인)"] += 1
                continue
            ret = [x["page"] for x in r["retrieved"]]
            cited = {x["page"] for x in r["citations"]}
            gold = set(r["pages"])
            ok = r.get("j_correct", {}).get("score", 0)
            if not (gold & set(ret)):
                c["검색 실패(정답 쪽이 상위 5에 없음)"] += 1
            elif not cited:
                c["인용 없음"] += 1
            elif not (cited & gold):
                c["인용 오류(정답 쪽을 찾았지만 다른 쪽 인용)" + ("" if ok else ", 답도 틀림")] += 1
            elif not ok:
                c["생성 오류(정답 쪽 인용, 답이 틀림)"] += 1
            else:
                c["성공"] += 1
    return c


def human_sheet(tag, n=20):
    rows = [r for r in read(R / tag / "eval_r0.judged.jsonl") if not r["abstained"]]
    random.Random(20).shuffle(rows)
    with open(R / "human_check.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["qid", "question", "gold_answer", "gold_pages", "answer", "cited_pages",
                    "judge_correct", "judge_reason", "human_correct(이다솔)", "human_correct(최도은)"])
        for r in rows[:n]:
            w.writerow([r["qid"], r["question"], r["answer"], r["pages"], r["text"],
                        sorted({c["page"] for c in r["citations"]}), r["j_correct"]["score"],
                        r["j_correct"].get("reason", ""), "", ""])


def main():
    out = {}
    lines = ["# LearnBoost MVP 벤치마크 결과", "",
             f"- 평가 데이터: Allganize RAG-Evaluation-Dataset-KO 문단형 128문항(원문 PDF 확보분), 보류 평가는 같은 128문항을 다른 분야 문서에 묻기",
             f"- 생성 모델: cyankiwi/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit (vLLM, RTX 4090), 검색: BGE-M3 + BM25(Kiwi) → RRF → bge-reranker-v2-m3, 상위 5문단",
             f"- 보류 기준 τ = {TAU} (개발셋 음성 최댓값 0.005 바로 위). 3회 반복 평균 ± 표준편차", ""]
    for name, tag, judge_dir in [("LearnBoost (판정기 30B)", "final-30b", R),
                                 ("LearnBoost (판정기 14B, 독립)", "final-30b", R / "judge14"),
                                 ("베이스라인: 검색 없는 LLM 단독 (판정기 30B)", "final-30b-base", R),
                                 ("베이스라인 (판정기 14B)", "final-30b-base", R / "judge14")]:
        for text_only in (False, True):
            s = summarize(judge_dir / tag, "eval", TAU, "eval_neg", text_only=text_only)
            key = f"{name} · {'텍스트 PDF 121' if text_only else '전체 128'}"
            out[key] = s
    (R / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    keys = list(LABEL)
    cols = list(out)
    lines += ["## 지표", "", "| 지표 | " + " | ".join(cols) + " | 목표 (잠정) |", "|---" * (len(cols) + 2) + "|"]
    for k in keys:
        lines.append(f"| {LABEL[k]} | " + " | ".join(fmt(out[c].get(k)) for c in cols)
                     + f" | {mark(k, out[cols[0]].get(k))} |")
    lines += ["", "목표 달성 표시는 첫 열(전체 128, 판정기 30B) 기준이다.", "", "## 실패 유형 (3회 합계, 384건)", ""]
    for k, v in failure_breakdown("final-30b").most_common():
        lines.append(f"- {k}: {v}")
    pm1 = tot = 0
    for p in sorted((R / "final-30b").glob("eval_r*.judged.jsonl")):
        for r in read(p):
            tot += 1
            cited = {c["page"] for c in r["citations"]} if not r["abstained"] else set()
            pm1 += any(abs(c - g) <= 1 for c in cited for g in r["pages"])
    lines += ["", f"- 참고: 인용 쪽이 정답 쪽 ±1 안에 드는 비율 {pm1 / tot:.3f}. "
              "'외교부-2024년 앙골라개황(저).pdf'는 받은 PDF가 정답 라벨보다 1쪽 짧아(47/48쪽) 9문항의 정답 쪽이 1쪽씩 밀린다."]
    s5 = [r for r in read(R / "final-30b" / "s5_r0.judged.jsonl")]
    hit = sum(bool({c["page"] for c in r["citations"]} & set(r["pages"])) for r in s5)
    acc = sum(r.get("j_correct", {}).get("score", 0) for r in s5)
    lines += ["", f"## S5 (Later) 표 · 그림 10문항", "", f"- 근거 쪽 인용 {hit}/10, 정답 {acc}/10 (영역 강조는 문단 단위라 표 · 그림 영역 강조는 아직 없다)"]
    (R / "REPORT.md").write_text("\n".join(lines) + "\n")
    human_sheet("final-30b")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
