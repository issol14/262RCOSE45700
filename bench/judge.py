"""LLM judge: answer correctness (O/X) and citation support (O/X).

usage: python -m bench.judge results/<tag>/*.jsonl   (writes <file>.judged.jsonl)
The judge model is whatever the vLLM server on LB_LLM_BASE_URL serves.
"""
import asyncio
import json
import re
import os
import sys
from pathlib import Path

from lb import generate

CORRECT = """너는 문서 질의응답 채점자다. [질문], [정답], [후보 답]을 읽고 후보 답이 맞는지 판정한다.
- 후보 답이 정답의 핵심 내용(숫자, 대상, 결론)을 담고 정답과 모순되지 않으면 1.
- 핵심이 빠졌거나 틀렸거나, 답을 하지 않았거나 "미확인"이면 0.
- 표현 차이와 덧붙인 설명은 감점하지 않는다. 다만 덧붙인 내용이 정답과 모순되면 0.
JSON 한 줄로만 답한다: {"score": 0 또는 1, "reason": "한 문장"}"""

SUPPORT = """너는 인용 검증자다. [질문], [답], 답이 인용한 [인용 문단]을 읽는다.
답의 핵심 주장이 인용 문단의 내용으로 뒷받침되면 1, 인용 문단에 없는 핵심 주장이 있거나 인용이 엉뚱하면 0.
JSON 한 줄로만 답한다: {"score": 0 또는 1, "reason": "한 문장"}"""


SUFFIX = os.environ.get("LB_JUDGE_SUFFIX", "judged")  # a second judge writes to another suffix
MODEL = os.environ.get("LB_JUDGE_MODEL")  # on a shared endpoint, name the judge explicitly
CONC = int(os.environ.get("LB_JUDGE_CONC", "24"))
SKIP_SUPPORT = os.environ.get("LB_JUDGE_SKIP_SUPPORT") == "1"  # correctness only (saves calls)


def strip_cites(t):
    return re.sub(r"\s*\[\d{1,2}\]", "", t)


def parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else {"score": 0, "reason": "parse fail: " + text[:80]}
    except Exception:
        s = re.search(r'"score"\s*:\s*([01])', text)
        return {"score": int(s.group(1)) if s else 0, "reason": "loose parse"}


async def ask(sem, system, user, model):
    async with sem:
        text, _, _ = await generate.chat([{"role": "system", "content": system},
                                          {"role": "user", "content": user}],
                                         model=model, temperature=0.0, max_tokens=200)
    return parse(text)


async def judge_file(path, sem, model):
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]

    async def one(r):
        if r["answerable"] and not r["abstained"]:
            r["j_correct"] = await ask(sem, CORRECT, f"[질문] {r['question']}\n\n[정답] {r['answer']}\n\n"
                                                     f"[후보 답] {strip_cites(r['text'])}", model)
        if not SKIP_SUPPORT and not r["abstained"] and r.get("citations"):
            cited = "\n".join(f"[{c['n']}] (p.{c['page']}) {c['text']}" for c in r["citations"])
            r["j_support"] = await ask(sem, SUPPORT, f"[질문] {r['question']}\n\n[답] {r['text']}\n\n"
                                                     f"[인용 문단]\n{cited}", model)
        r["judge_model"] = model
        return r

    rows = await asyncio.gather(*(one(r) for r in rows))
    out = Path(str(path).replace(".jsonl", f".{SUFFIX}.jsonl"))
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print(out, flush=True)


async def main(paths):
    model = MODEL or (await generate.client().models.list()).data[0].id
    sem = asyncio.Semaphore(CONC)
    for p in paths:
        if ".judged" in p:
            continue
        await judge_file(p, sem, model)


asyncio.run(main(sys.argv[1:]))
