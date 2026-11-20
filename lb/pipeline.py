"""One question → retrieval → abstain gate → cited answer."""
import asyncio
import time

from . import config, generate, retrieve


async def answer(doc_id, question, selection=None, mode="hybrid_rerank", use_rewrite=False,
                 tau=None, model=None, temperature=0.7, seed=None, on_token=None, passages=None,
                 history=None, doc_names=None, memory=None, on_step=None, region_image=None):
    """`doc_id` is one document (bench) or a list of a space's documents (app).

    With `history` (recent [(question, answer)] of the chat) a follow-up is first resolved
    into a standalone question, which then drives retrieval and the reranker gate.

    `on_step(key, label, detail=None, done=False)` (async) narrates the stages for the
    live processing log; the bench leaves it unset and the path is unchanged.

    `region_image` (a data URL of the area the learner dragged, S5) sends the question to a
    vision model with that image. The learner pointed at the evidence, so the reranker gate
    does not apply; the model can still answer "미확인".
    """
    async def step(key, label, detail=None, done=False):
        if on_step:
            await on_step(key, label, detail, done)

    tau = config.ABSTAIN_TAU if tau is None else tau
    t0 = time.perf_counter()
    standalone = None
    has_selection = bool(selection and (selection.get("text") or region_image))
    # A selection question ("이게 무슨 뜻이에요?") points at the selected text, not at the
    # previous turn; resolving it against the chat made it follow the last topic (11/15).
    if history and not has_selection:
        await step("context", "앞 대화에 이어서 질문 이해하기")
        standalone = await generate.contextualize(history, question, model=model)
        await step("context", "앞 대화에 이어서 질문 이해하기",
                   f"“{standalone}”" if standalone and standalone != question else "그대로 검색", True)
    q = standalone or question
    rq = q
    if has_selection and selection.get("text"):
        rq = f"{selection['text']}\n{q}"
    queries = [rq]
    rewritten = None
    pool = {"n": None}
    if passages is None:
        if use_rewrite:
            rewritten = await generate.rewrite(rq, model=model)
            queries.append(rewritten)
        if isinstance(doc_id, (list, tuple)):
            n_docs = len(doc_id)
            await step("search", f"문서 {n_docs}개에서 근거 후보 찾기")
            if on_step:
                loop = asyncio.get_running_loop()

                def on_pool(n):  # called from the retrieval thread
                    pool["n"] = n
                    asyncio.run_coroutine_threadsafe(step("search", f"문서 {n_docs}개에서 근거 후보 찾기", f"후보 {n}개", True), loop)
                    asyncio.run_coroutine_threadsafe(step("rerank", f"후보 {n}개를 질문과 맞춰 다시 순위 매기기"), loop)

                passages = await asyncio.to_thread(retrieve.retrieve_many, list(doc_id), queries, rq, None, on_pool)
            else:
                passages = retrieve.retrieve_many(list(doc_id), queries, rerank_query=rq)
        else:
            passages = retrieve.retrieve(doc_id, queries, mode=mode, rerank_query=rq)
    t_ret = time.perf_counter() - t0
    top = max((p["rerank"] for p in passages if p["rerank"] is not None), default=None)
    res = {"question": question, "standalone": standalone, "rewritten": rewritten, "retrieved": [
        {"doc_id": p.get("doc_id"), "page": p["chunk"]["page"], "chunk_id": p["chunk"]["id"], "rerank": p["rerank"]}
        for p in passages],
        "top_score": top, "retrieval_s": t_ret}
    if on_step:
        pages = ", ".join(dict.fromkeys(
            (f"{(doc_names or {}).get(p.get('doc_id'), '')} " if len({x.get('doc_id') for x in passages}) > 1 else "")
            + f"p.{p['chunk']['page']}" for p in passages))
        await step("rerank", f"후보 {pool['n']}개를 질문과 맞춰 다시 순위 매기기" if pool["n"] else "후보를 질문과 맞춰 다시 순위 매기기",
                   f"근거 {len(passages)}개 · {pages}" + (f" · 최고 점수 {top:.2f}" if top is not None else "") if passages else "근거 없음", True)
    if not region_image and (not passages or (top is not None and top < tau)):
        await step("gate", "근거가 충분한지 확인", "기준에 못 미쳐 답하지 않아요", True)
        res.update(text=generate.ABSTAIN, abstained=True, gate=True, citations=[], ttft_s=None,
                   total_s=time.perf_counter() - t0)
        return res
    msgs = [{"role": "system", "content": generate.SYSTEM},
            {"role": "user", "content": generate.build_user(question, passages, selection, standalone=standalone,
                                                           doc_names=doc_names, memory=memory)}]
    if region_image:
        msgs[1]["content"] = generate.region_user((msgs[1]["content"], region_image), selection.get("page", "?"))
        await step("write", "고른 영역의 이미지를 읽고 답 쓰기")
    else:
        await step("write", "근거를 인용해 답 쓰기")
    text, ttft, _ = await generate.chat(msgs, model=model, temperature=temperature, seed=seed, on_token=on_token)
    text = generate.normalize_citations(text, len(passages))
    abstained = generate.is_abstain(text)
    res.update(text=text, abstained=abstained, gate=False,
               citations=[] if abstained else generate.parse_citations(text, passages),
               ttft_s=(ttft + t_ret) if ttft is not None else None, total_s=time.perf_counter() - t0)
    await step("write", "고른 영역의 이미지를 읽고 답 쓰기" if region_image else "근거를 인용해 답 쓰기",
               "문서에 답이 없어 미확인" if abstained else f"인용 {len(res['citations'])}개 · {res['total_s']:.1f}초", True)
    return res


async def closed_book(question, model=None, temperature=0.7, seed=None):
    t0 = time.perf_counter()
    text, ttft, _ = await generate.chat([{"role": "system", "content": generate.CLOSED_BOOK},
                                         {"role": "user", "content": question}],
                                        model=model, temperature=temperature, seed=seed)
    return {"question": question, "text": text, "abstained": generate.is_abstain(text), "citations": [],
            "retrieved": [], "ttft_s": ttft, "total_s": time.perf_counter() - t0}
