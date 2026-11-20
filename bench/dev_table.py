"""Dev-set comparison table (model × retrieval) used to pick the config. Writes results/DEV.md."""
from lb import config
from bench.metrics import summarize

R = config.ROOT / "results"
rows = [("Qwen3-8B AWQ", "hybrid+rerank", "dev-8b-hybrid_rerank"),
        ("Qwen3-14B AWQ", "dense", "dev-14b-dense"),
        ("Qwen3-14B AWQ", "hybrid", "dev-14b-hybrid"),
        ("Qwen3-14B AWQ", "hybrid+rerank", "dev-14b-hybrid_rerank"),
        ("Qwen3-14B AWQ", "hybrid+rerank+rewrite", "dev-14b-hr-rw"),
        ("Qwen3-30B-A3B AWQ", "hybrid+rerank", "dev-30b-hybrid_rerank"),
        ("Qwen3-8B AWQ", "검색 없음", "dev-8b-base"),
        ("Qwen3-14B AWQ", "검색 없음", "dev-14b-base"),
        ("Qwen3-30B-A3B AWQ", "검색 없음", "dev-30b-base")]
out = ["# 개발셋(텍스트형 29문항, 스캔 PDF 2문항 포함) 비교", "",
       "판정기 = Qwen3-30B-A3B. 1회 실행이라 1문항(0.034) 차이는 잡음 수준이다.", "",
       "| 생성 모델 | 검색 | 정답률 | 인용 근거 일치율 | 근거 페이지 일치율 | 과잉 보류율 | 보류율(다른 문서) | 응답 시간 중앙값(초) |",
       "|---|---|---|---|---|---|---|---|"]
for m, mode, tag in rows:
    neg = "dev_neg" if list((R / tag).glob("dev_neg_r*.jsonl")) else None
    s = summarize(R / tag, "dev", config.ABSTAIN_TAU, neg)
    g = lambda k: "–" if s.get(k) is None else f"{s[k]['mean']:.3f}"
    out.append(f"| {m} | {mode} | {g('accuracy')} | {g('citation_support')} | {g('page_hit')} | {g('over_abstain')} | {g('abstain_rate')} | {g('latency_median_s')} |")
(R / "DEV.md").write_text("\n".join(out) + "\n")
print("\n".join(out))
