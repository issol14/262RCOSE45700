"""Concept list: the learner profile sent with a question, and suggestions after an answer."""
import json
import re

from fastapi import APIRouter, HTTPException, Request

from lb import generate

from .common import read_json
from .prompts import PROFILE_GUIDE, SUGGEST

router = APIRouter(prefix="/api")


def valid_profile(profile):
    def ok(c):
        return (isinstance(c, dict) and isinstance(c.get("id"), str) and len(c["id"]) <= 100
                and isinstance(c.get("name"), str) and c["name"].strip() and len(c["name"]) <= 80
                and isinstance(c.get("category"), str) and len(c["category"]) <= 60
                and isinstance(c.get("level"), int) and not isinstance(c["level"], bool) and 1 <= c["level"] <= 5
                and isinstance(c.get("locked"), bool))
    return isinstance(profile, list) and all(ok(c) for c in profile)


def profile_text(profile):
    if not profile:
        return ""
    concepts = [{"name": c["name"], "category": c["category"], "level": c["level"]} for c in profile]
    return f"{PROFILE_GUIDE}\n{json.dumps(concepts, ensure_ascii=False)}"


def _bounded(v, n):
    return v.strip()[:n] if isinstance(v, str) else ""


def parse_suggestions(text, profile):
    """Keep only what the screens may apply: up to 3 new concepts, and one-step level changes
    of unlocked saved concepts."""
    try:
        parsed = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I))
    except (ValueError, TypeError):
        return {"additions": [], "updates": []}
    if not isinstance(parsed, dict):
        return {"additions": [], "updates": []}
    adds = parsed.get("additions") if isinstance(parsed.get("additions"), list) else []
    additions = [a for a in ({"name": _bounded((i or {}).get("name") if isinstance(i, dict) else None, 80),
                              "category": _bounded(i.get("category") if isinstance(i, dict) else None, 60),
                              "notes": _bounded(i.get("notes") if isinstance(i, dict) else None, 600)} for i in adds[:3])
                 if a["name"]]
    by_id = {c["id"]: c for c in profile}
    updates = []
    for u in parsed.get("updates") if isinstance(parsed.get("updates"), list) else []:
        c = by_id.get(u.get("id")) if isinstance(u, dict) else None
        level = u.get("level") if isinstance(u, dict) else None
        if (c and not c["locked"] and isinstance(level, int) and not isinstance(level, bool)
                and 1 <= level <= 5 and abs(level - c["level"]) == 1):
            updates.append({"id": c["id"], "level": level})
    return {"additions": additions, "updates": updates[:5]}


@router.post("/concepts/suggest")
async def suggest(request: Request):
    body = await read_json(request)
    profile, question, answer = body.get("learningProfile", []), body.get("question"), body.get("answer")
    if (not valid_profile(profile) or not isinstance(question, str) or not question.strip() or len(question) > 4000
            or not isinstance(answer, str) or len(answer) > 6000):
        raise HTTPException(400, "개념을 정리할 대화 내용을 확인해 주세요.")
    try:
        text, _, _ = await generate.chat(
            [{"role": "system", "content": SUGGEST},
             {"role": "user", "content": json.dumps({"profile": profile, "question": question, "answer": answer}, ensure_ascii=False)}],
            temperature=0.0, max_tokens=1500)
    except Exception:
        raise HTTPException(503, "생성 서버 연결을 확인해 주세요.")
    return parse_suggestions(text, profile)
