"""Parsing, indexing, retrieval, citations: the parts the benchmark numbers rest on."""
import asyncio

import numpy as np

from lb import generate, index, pipeline, retrieve
from lb.index import BM25
from lb.parse import chunk_pdf


def test_chunks_stay_on_one_page_and_carry_normalized_boxes(pdf):
    chunks, n_pages = chunk_pdf(pdf)
    assert n_pages == 3
    assert [c["page"] for c in chunks] == sorted(c["page"] for c in chunks)
    assert {c["page"] for c in chunks} == {1, 2, 3}
    for c in chunks:
        assert len(c["rects"]) == len(c["blocks"]) >= 1
        for x0, y0, x1, y1 in c["rects"]:
            assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert "TTFT" in next(c["text"] for c in chunks if c["page"] == 2)


def test_bm25_prefers_the_passage_that_holds_the_query_terms():
    docs = [index.tokenize(t) for t in ("리랭킹은 후보 문단의 순위를 다시 매긴다", "배치 크기와 처리량", "첫 토큰 시간 TTFT")]
    scores = BM25(docs).scores(index.tokenize("리랭킹 순위"))
    assert int(np.argmax(scores)) == 0 and scores[1] == 0


def test_rrf_rewards_agreement_between_rankings():
    assert retrieve._rrf([[3, 1, 2], [1, 3, 2]])[:2] in ([1, 3], [3, 1])
    assert retrieve._rrf([[5, 1], [1, 9], [1, 5]])[0] == 1


def test_retrieval_returns_the_page_that_answers(pdf):
    index.build("t-retr", pdf)
    top = retrieve.retrieve("t-retr", ["TTFT는 무엇인가요?"])
    assert top[0]["chunk"]["page"] == 2 and top[0]["rerank"] is not None
    many = retrieve.retrieve_many(["t-retr"], ["배치 크기를 키우면?"])
    assert many[0]["chunk"]["page"] == 3 and many[0]["doc_id"] == "t-retr"


def _passages(*texts):
    return [{"chunk": {"id": i, "page": i + 1, "text": t, "rects": [[0.1, 0.1, 0.9, 0.2]], "blocks": [t]}, "rerank": 0.9}
            for i, t in enumerate(texts)]


def test_citations_only_point_at_supplied_passages():
    ps = _passages("가", "나")
    cites = generate.parse_citations("첫 문장 [1]. 둘째 [2][1]. 없는 번호 [7].", ps)
    assert [c["n"] for c in cites] == [1, 2] and [c["page"] for c in cites] == [1, 2]


def test_foreign_citation_marks_fold_into_ours():
    assert generate.normalize_citations("답이다【2†L3-L5】.", 3) == "답이다[2]."
    assert generate.normalize_citations("답【1】 그리고 【9†x】", 3) == "답[1] 그리고 【9†x】"
    assert generate.normalize_citations("그대로 [1]", 3) == "그대로 [1]"


def test_document_footnote_marks_cannot_pose_as_citations():
    user = generate.build_user("질문", _passages("본문 [6] 각주 표시"))
    assert "(6)" in user and "[6]" not in user and "[1] (p.1)" in user


def test_abstain_is_the_bare_word_only():
    assert generate.is_abstain('"미확인"') and generate.is_abstain("미확인.")
    assert not generate.is_abstain("미확인이라고 볼 수도 있지만 문서 3쪽에 따르면 값은 12이며 조건은 다음과 같다 [1].")


def test_gate_abstains_without_calling_the_generator(pdf, llm):
    index.build("t-gate", pdf)
    res = asyncio.run(pipeline.answer("t-gate", "zzzz qqqq xxxx"))
    assert res["abstained"] and res["gate"] and res["text"] == generate.ABSTAIN and llm.calls == []


def test_answer_cites_and_keeps_bench_prompt_shape(pdf, llm):
    index.build("t-ans", pdf)
    llm.reply = "첫 토큰까지 걸린 시간이다 [1]."
    res = asyncio.run(pipeline.answer("t-ans", "TTFT는 첫 토큰이 나올 때까지 걸린 시간인가요?"))
    assert not res["abstained"] and res["citations"][0]["page"] == 2
    system, user = llm.calls[0]["messages"]
    assert system["content"] == generate.SYSTEM and isinstance(user["content"], str)
    assert user["content"].startswith("[문단]\n[1] (p.2)")


def test_selection_question_is_not_rewritten_from_the_chat(pdf, llm):
    index.build("t-sel", pdf)
    llm.reply = "첫 토큰 시간이다 [1]."
    sel = {"page": 2, "text": "TTFT는 첫 토큰이 나올 때까지 걸린 시간이다.", "rects": [[0.1, 0.1, 0.9, 0.2]]}
    res = asyncio.run(pipeline.answer(["t-sel"], "이게 무슨 뜻이에요?", selection=sel,
                                      history=[("리랭킹이 뭐예요?", "순위를 다시 매긴다 [1].")]))
    assert res["standalone"] is None and len(llm.calls) == 1       # no contextualize call
    assert res["retrieved"][0]["page"] == 2


def test_region_question_skips_the_gate_and_sends_the_image(pdf, llm):
    index.build("t-reg", pdf)
    llm.reply = "표의 값은 12다."
    sel = {"page": 3, "text": "", "rects": [[0.1, 0.1, 0.5, 0.3]]}
    res = asyncio.run(pipeline.answer(["t-reg"], "zzzz qqqq", selection=sel, region_image="data:image/jpeg;base64,AAAA",
                                      model="vl"))
    assert not res["gate"] and not res["abstained"]
    content = llm.calls[-1]["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg") and "p.3" in content[0]["text"]
    assert llm.calls[-1]["model"] == "vl"
