"""Answer generation through an OpenAI-compatible server (vLLM in dev, RNGD later)."""
import asyncio
import re
import time

from openai import AsyncOpenAI

from . import config

ABSTAIN = "미확인"

SYSTEM = """너는 LearnBoost다. 학습자가 올린 문서를 함께 읽으며 이해를 돕는 튜터다.

규칙
1. [문단]에 있는 내용만 근거로 답한다. 문단에 없는 사실을 바깥 지식으로 보태지 않는다.
2. 근거로 삼은 문장 끝에 그 문단 번호를 [1]처럼 붙인다. 번호는 [문단] 목록의 번호만 쓴다.
3. [문단]에서 질문의 답을 찾을 수 없으면 다른 말 없이 "미확인"이라고만 쓴다.
4. 질문 유형에 맞는 설명 틀로 답한다.
   - 뜻을 묻는 질문: 한 문장으로 뜻을 말하고, 이 문서에서 어떤 의미로 쓰였는지 덧붙인다.
   - 문서 속 사실·의미를 묻는 질문: 핵심 답을 먼저 한두 문장으로 말하고, 근거가 되는 세부를 잇는다.
   - 흐름 속 역할을 묻는 질문: 앞뒤 맥락을 짚고, 이 부분이 하는 역할을 말한다.
   - 절차나 여러 항목: 번호 목록으로 나눠 설명한다.
5. 한국어로, 군더더기 없이 쓴다. "[문단]"이라는 말은 답에 쓰지 않는다."""

NOTE = """너는 학습자의 교재 여백에 필기를 남긴다. 질문과 답을 보고, 나중에 다시 볼 때 바로 떠오르도록
핵심만 한두 문장으로 적는다. 규칙: 60자 안팎, 명사형이나 "~다"로 끝맺기, 인용 번호·따옴표·머리말 없이 필기 문장만."""

CLOSED_BOOK = """너는 질문에 답하는 도우미다. 질문에 대해 아는 내용으로 한국어로 간결하게 답한다.
확실히 알지 못하면 다른 말 없이 "미확인"이라고만 쓴다."""

CONTEXTUALIZE = """앞 대화를 보고 마지막 질문을 앞 대화 없이도 뜻이 통하는 질문 한 문장으로 다시 써라.
- "그건", "이거", "그럼", "아까 그거"처럼 앞 대화를 가리키는 말은 앞 대화에 나온 구체적인 대상으로 바꾼다.
- 마지막 질문이 이미 혼자 뜻이 통하면 한 글자도 바꾸지 말고 그대로 쓴다.
- 앞 대화에 없는 용어·사실을 보태지 않는다. 답하지 말고 질문만 출력한다."""

REWRITE = """다음 질문을 문서 검색에 쓸 질의로 한 줄로 다시 써라. 질문의 핵심 용어·숫자·고유명사를 빠짐없이 남기고, 같은 뜻의 다른 표현을 한두 개 덧붙여라. 질의만 출력한다."""

_client = None


STREAM_IDLE_S = 45  # seconds without a chunk before a stream counts as stalled


def client():
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY, timeout=600)
    return _client


def _neutralize(text):
    # documents carry their own "[6]"-style footnote marks; turn them into "(6)" so
    # every [n] in the answer can only mean one of our passages
    return re.sub(r"\[(\d{1,3})\]", r"(\1)", text)


def build_user(question, passages, selection=None, standalone=None, doc_names=None, memory=None):
    """The user turn. Bench calls pass only the first three arguments; the rest is the app's
    conversation context: `standalone` (follow-up resolved against the chat), `doc_names`
    (label passages when they come from several documents), `memory` (learner context)."""
    lines = []
    if memory:
        lines += ["[학습자 메모] 근거가 아니다. 설명의 수준과 방식을 맞추는 데만 쓴다.", memory.strip(), ""]
    lines.append("[문단]")
    multi = doc_names and len({p.get("doc_id") for p in passages}) > 1
    for n, p in enumerate(passages, 1):
        where = f"{doc_names.get(p.get('doc_id'), '문서')} p.{p['chunk']['page']}" if multi else f"p.{p['chunk']['page']}"
        lines.append(f"[{n}] ({where}) {_neutralize(p['chunk']['text'])}")
    if selection and selection.get("text"):
        lines += ["", f"[학습자가 고른 문장] (p.{selection.get('page', '?')}) {_neutralize(selection['text'])}"]
    lines += ["", f"[질문] {question}"]
    if standalone and standalone != question:
        lines.append(f"[앞 대화를 반영한 질문] {standalone}")
    return "\n".join(lines)


def _extra(model):
    # gpt-oss takes a reasoning effort; Qwen3 / EXAONE hybrids: turn thinking off for latency
    if "gpt-oss" in model:
        return {"reasoning_effort": "low"}
    if "Qwen3-VL" in model:   # the vision model takes no template switches
        return {}
    return {"chat_template_kwargs": {"enable_thinking": False}}


async def chat(messages, model=None, temperature=0.7, top_p=0.8, max_tokens=700, seed=None, on_token=None):
    """Stream a completion. Returns (text, ttft_s, total_s)."""
    model = model or config.LLM_MODEL
    t0 = time.perf_counter()
    ttft = None
    out = []
    stream = await client().chat.completions.create(
        model=model, messages=messages, temperature=temperature, top_p=top_p,
        max_tokens=max_tokens, seed=seed, stream=True, extra_body=_extra(model))
    it = stream.__aiter__()
    while True:
        # an endpoint stream can stall mid-answer; fail instead of hanging the chat
        try:
            ev = await asyncio.wait_for(it.__anext__(), timeout=STREAM_IDLE_S)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            await stream.close()
            raise RuntimeError(f"생성 서버가 {STREAM_IDLE_S}초 동안 응답하지 않았습니다")
        if not ev.choices:
            continue
        d = ev.choices[0].delta.content or ""
        if d:
            if ttft is None:
                ttft = time.perf_counter() - t0
            out.append(d)
            if on_token:
                await on_token(d)
    text = re.sub(r"<think>.*?</think>", "", "".join(out), flags=re.S).strip()
    return text, ttft, time.perf_counter() - t0


async def rewrite(question, model=None):
    text, _, _ = await chat([{"role": "system", "content": REWRITE},
                             {"role": "user", "content": question}],
                            model=model, temperature=0.0, max_tokens=120)
    return text.splitlines()[0].strip() if text else question


def _plain(text, limit=300):
    t = re.sub(r"\[\d{1,2}\]", "", text).strip()
    return t[:limit] + ("…" if len(t) > limit else "")


async def contextualize(history, question, model=None):
    """Resolve a follow-up ("그럼 그건 …?") into a standalone question from recent turns.

    `history` is [(question, answer)], oldest first; abstained answers carry no content.
    """
    lines = []
    for q, a in history:
        lines.append(f"학습자: {q}")
        if a and not is_abstain(a):
            lines.append(f"튜터: {_plain(a)}")
    lines += ["", f"마지막 질문: {question}"]
    text, _, _ = await chat([{"role": "system", "content": CONTEXTUALIZE},
                             {"role": "user", "content": "\n".join(lines)}],
                            model=model, temperature=0.0, max_tokens=120)
    out = text.splitlines()[0].strip().strip('"') if text else ""
    return out or question


def normalize_citations(text, n_passages):
    """Some chat templates (gpt-oss) cite as 【2†L3-L5】; fold those into our [2].

    Marks whose number is not one of the passages are left alone, so nothing new becomes a citation.
    """
    def fold(m):
        n = int(m.group(1))
        return f"[{n}]" if 1 <= n <= n_passages else m.group(0)
    return re.sub(r"【(\d+)(?:†[^】\r\n]{1,48})?】", fold, text)


def region_user(content, page):
    """User turn for an S5 region question: the usual text plus the cropped image."""
    text, image_url = content
    return [{"type": "text", "text": f"{text}\n\n첨부한 이미지는 학습자가 문서 p.{page}에서 직접 고른 영역(표 · 그림)이다. "
                                     "이미지에서 읽은 내용으로 답하고, [문단]으로도 뒷받침되는 문장에는 그 번호를 붙인다. "
                                     "이미지에서도 [문단]에서도 답을 찾을 수 없으면 \"미확인\"이라고만 쓴다."},
            {"type": "image_url", "image_url": {"url": image_url}}]


def is_abstain(text: str) -> bool:
    t = text.strip().strip('"').strip()
    return t.startswith(ABSTAIN) and len(t) < 40


def _focus_rects(chunk, text, n):
    """Rects of the blocks in `chunk` that the sentences citing [n] draw on.

    Falls back to the whole chunk when blocks are unknown or nothing overlaps.
    """
    blocks = chunk.get("blocks")
    if not blocks or len(blocks) != len(chunk["rects"]):
        return chunk["rects"]
    from .index import tokenize
    # the claim behind each "[n]" is the text just before it, back to the previous
    # citation mark or line break
    claims = []
    for m in re.finditer(rf"\[{n}\]", text):
        head = text[max(0, m.start() - 200):m.start()]
        head = re.split(r"\[\d{1,2}\]|\n", head)[-1]
        claims.append(head)
    want = set(tokenize(" ".join(claims) or text))
    if not want:
        return chunk["rects"]
    scores = [len(want & set(tokenize(b))) / (len(set(tokenize(b))) ** 0.5 or 1) for b in blocks]
    best = max(scores)
    if best <= 0:
        return chunk["rects"]
    top = sorted(range(len(scores)), key=lambda i: -scores[i])[:3]
    return [chunk["rects"][i] for i in sorted(top) if scores[i] >= 0.7 * best]


def parse_citations(text, passages):
    seen, cites = set(), []
    for m in re.finditer(r"\[(\d{1,2})\]", text):
        n = int(m.group(1))
        if 1 <= n <= len(passages) and n not in seen:
            seen.add(n)
            c = passages[n - 1]["chunk"]
            cites.append({"n": n, "doc_id": passages[n - 1].get("doc_id"), "page": c["page"], "chunk_id": c["id"],
                          "rects": _focus_rects(c, text, n), "text": c["text"]})
    return cites


async def note(question, answer, model=None):
    """Short margin note for S4. Demo: a second call; spec wants it in the answer call (TODO)."""
    text, _, _ = await chat([{"role": "system", "content": NOTE},
                             {"role": "user", "content": f"[질문] {question}\n[답] {re.sub(r'\[\d{1,2}\]', '', answer)}"}],
                            model=model, temperature=0.3, max_tokens=160)
    return text.strip().strip('"')
