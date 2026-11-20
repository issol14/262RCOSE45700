"""The API the screens call, end to end with the stand-in models of conftest."""
import json
import time

import fitz
import pytest
from fastapi.testclient import TestClient

from lb import config
from server import chat as chat_api
from server.app import app

from .conftest import make_pdf

WS = "ws-lecture"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def upload(client, path, workspace=WS, name=None):
    r = client.post("/api/documents", data={"workspace": workspace},
                    files={"file": (name or path.name, path.read_bytes(), "application/pdf")})
    assert r.status_code == 201, r.text
    for _ in range(100):
        d = next(x for x in client.get("/api/documents", params={"workspace": workspace}).json() if x["id"] == r.json()["id"])
        if not d["indexing"]:
            return d
        time.sleep(0.05)
    raise AssertionError("indexing did not finish")


@pytest.fixture(scope="module")
def doc(client, tmp_path_factory):
    d = upload(client, make_pdf(tmp_path_factory.mktemp("ws") / "강의.pdf"), name="C:\\tmp\\강의.pdf")
    assert d["indexed"] and d["indexStatus"] == "ready" and d["pages"] == 3 and d["extension"] == ".pdf", d
    assert d["name"] == "강의.pdf" and d["workspaceId"] == WS and "준비 완료" in d["indexLog"]
    return d


def chat(client, **body):
    body = {"stream": True, "workspaceId": WS, **body}
    r = client.post("/api/chat", json=body)
    assert r.status_code == 200, r.text
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    return events, events[-1]


def user(text):
    return [{"role": "user", "content": text}]


def test_failures_are_reported_as_an_error_message(client):
    r = client.get("/api/documents/nope/text")
    assert r.status_code == 404 and "error" in r.json()
    assert "error" in client.get("/api/unknown").json()


def test_requests_from_another_site_are_refused(client):
    assert client.get("/api/records", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/records", headers={"origin": "http://testserver"}).status_code == 200


def test_config_offers_the_vision_model_and_never_the_key(client):
    c = client.get("/api/config").json()
    assert config.VL_MODEL in [m["id"] for m in c["models"]] and c["model"] == config.LLM_MODEL
    assert config.LLM_API_KEY not in json.dumps(c)


def test_upload_rejects_what_it_cannot_read(client, tmp_path):
    post = lambda name, data: client.post("/api/documents", data={"workspace": WS}, files={"file": (name, data, "application/pdf")})
    r = post("메모.txt", b"hello")
    assert r.status_code == 400 and "PDF" in r.json()["error"]
    assert post("a.pdf", b"not a pdf").status_code == 400
    blank = fitz.open()
    blank.new_page()
    assert "스캔" in post("scan.pdf", blank.tobytes()).json()["error"]
    assert client.post("/api/documents", files={"file": ("a.pdf", b"x", "application/pdf")}).status_code == 422


def test_documents_belong_to_their_workspace(client, doc, tmp_path, llm):
    other = upload(client, make_pdf(tmp_path / "다른공간.pdf"), workspace="ws-other")
    here = [d["id"] for d in client.get("/api/documents", params={"workspace": WS}).json()]
    assert doc["id"] in here and other["id"] not in here
    # a chat cannot ask about a document of another workspace
    _, last = chat(client, messages=user("TTFT가 무엇인가요?"), documentIds=[other["id"]])
    assert last["type"] == "error" and llm.calls == []


def test_document_text_is_the_positioned_chunks(client, doc):
    chunks = client.get(f"/api/documents/{doc['id']}/text").json()
    assert {c["page"] for c in chunks} == {1, 2, 3} and all(c["text"] for c in chunks)


def test_cited_answer_carries_rects_and_a_margin_note(client, doc, llm):
    llm.reply = lambda m: "핵심만 적은 필기" if "여백에 필기" in m[0]["content"] else "TTFT는 첫 토큰까지 걸린 시간이다 [1]."
    events, done = chat(client, messages=user("TTFT가 무엇인가요?"), documentIds=[doc["id"]], documentOnly=True)
    assert done["type"] == "done" and not done.get("abstained")
    assert "".join(e["content"] for e in events if e["type"] == "delta") == done["content"]
    assert any(e["type"] == "progress" for e in events)
    first = done["sources"][0]
    assert first["documentId"] == doc["id"] and first["page"] == 2 and first["name"] == "강의.pdf"
    assert first["rects"] and all(len(r) == 4 and 0 <= min(r) and max(r) <= 1 for r in first["rects"])
    assert done["pdfNote"] == {"number": 1, "memo": "핵심만 적은 필기"}
    assert done["latencyS"] >= 0 and done["topScore"] > config.ABSTAIN_TAU


def test_unsupported_question_is_held_back_without_a_generation_call(client, doc, llm):
    _, done = chat(client, messages=user("화성의 대기압은 얼마인가요?"), documentIds=[doc["id"]], documentOnly=True)
    assert done["abstained"] and done["gate"] and done["content"] == chat_api.ABSTAIN_TEXT and done["sources"] == []
    assert llm.calls == []


def test_outside_the_documents_is_answered_and_marked_when_the_learner_allows_it(client, doc, llm):
    llm.reply = "일반 지식으로 답합니다."
    events, done = chat(client, messages=user("화성의 대기압은 얼마인가요?"), documentIds=[doc["id"]], documentOnly=False)
    assert done["ungrounded"] and done["content"] == "일반 지식으로 답합니다." and done["sources"] == []
    assert "미확인" not in "".join(e["content"] for e in events if e["type"] == "delta")


def test_a_model_level_abstention_never_reaches_the_screen(client, doc, llm):
    llm.reply = "미확인"
    events, done = chat(client, messages=user("TTFT가 무엇인가요?"), documentIds=[doc["id"]], documentOnly=True)
    assert done["abstained"] and not done["gate"]
    assert [e for e in events if e["type"] == "delta"] == []


def test_what_the_learner_still_finds_confusing_reaches_the_prompt(client, doc, llm):
    llm.reply = "TTFT는 첫 토큰까지 걸린 시간이다 [1]."
    chat(client, messages=user("TTFT가 무엇인가요?"), documentIds=[doc["id"]], context="아직 헷갈려하는 곳: 2쪽 TTFT가 왜 길어지나요")
    assert "아직 헷갈려하는 곳" in json.dumps(llm.calls[0]["messages"], ensure_ascii=False)


def test_chat_without_documents_is_plain_conversation_with_the_learner_profile(client, llm):
    llm.reply = "안녕하세요."
    profile = [{"id": "c1", "name": "리랭킹", "category": "검색", "level": 4, "locked": False}]
    _, done = chat(client, messages=user("안녕"), learningProfile=profile, context="시험 대비")
    assert done["content"] == "안녕하세요." and done["sources"] == []
    system = llm.calls[0]["messages"][0]["content"]
    assert "리랭킹" in system and "No source documents" in system
    assert "시험 대비" in llm.calls[0]["messages"][1]["content"]


def test_region_question_goes_to_the_vision_model_with_an_image(client, doc, llm):
    llm.reply = "표의 내용입니다 [1]."
    quote = {"documentId": doc["id"], "page": 2, "endPage": 2, "text": "선택한 영역",
             "region": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.3}}
    _, done = chat(client, messages=user("이 영역은?"), documentIds=[doc["id"]], documentQuote=quote, model=config.LLM_MODEL)
    assert done["model"] == config.VL_MODEL
    call = next(c for c in llm.calls if isinstance(c["messages"][-1]["content"], list))
    assert call["model"] == config.VL_MODEL and call["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg")
    bad = {**quote, "region": {"x": 0.9, "y": 0, "width": 0.5, "height": 0.1}}
    events, last = chat(client, messages=user("이 영역은?"), documentQuote=bad)
    assert last["type"] == "error"


def test_search_only_returns_passages_and_calls_no_generator(client, doc, llm):
    r = client.post("/api/chat", json={"messages": user("리랭킹"), "workspaceId": WS, "documentIds": [doc["id"]], "searchOnly": True})
    assert r.status_code == 200 and r.json()["sources"][0]["page"] == 1 and llm.calls == []


def test_malformed_chat_is_refused(client):
    assert client.post("/api/chat", json={"messages": []}).status_code == 400
    assert client.post("/api/chat", json={"messages": user("q"), "learningProfile": [{"id": 1}]}).status_code == 400
    assert client.post("/api/chat", json={"messages": user("q"), "workspaceId": 3}).status_code == 400
    _, last = chat(client, messages=user("q"), documentIds=["missing"])
    assert last["type"] == "error"


def test_annotated_pdf_places_notes_by_rects_then_excerpt_then_corner(client, doc):
    notes = [
        {"page": 2, "rects": [[0.1, 0.1, 0.9, 0.15]], "excerpt": "", "question": "q1", "answer": "rects로 둔 필기", "status": "understood"},
        {"page": 1, "excerpt": "크로스 인코더는 질문과 문단을 함께", "question": "q2", "answer": "본문에서 찾아 둔 필기"},
        {"page": 3, "excerpt": "", "question": "q3", "answer": "자리를 모르는 필기"},
    ]
    r = client.post(f"/api/documents/{doc['id']}/annotated-file", json={"notes": notes})
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    pdf = fitz.open(stream=r.content, filetype="pdf")
    kinds = [[a.type[1] for a in page.annots()] for page in pdf]
    assert kinds[1].count("Highlight") == 1 and kinds[0].count("Highlight") == 1 and kinds[2] == ["Text"]
    # a spot the learner marked 이해했어요 is drawn in gray, one still 헷갈려요 in yellow
    stroke = lambda page: next(a for a in pdf[page].annots() if a.type[1] == "Highlight").colors["stroke"]
    assert stroke(1)[2] > 0.8 and stroke(0)[2] < 0.6
    assert client.post(f"/api/documents/{doc['id']}/annotated-file", json={"notes": []}).status_code == 400


def test_concept_suggestions_keep_only_what_the_screens_may_apply(client, llm):
    profile = [{"id": "a", "name": "리랭킹", "category": "검색", "level": 3, "locked": False},
               {"id": "b", "name": "TTFT", "category": "성능", "level": 3, "locked": True}]
    llm.reply = "```json\n" + json.dumps({
        "additions": [{"name": "크로스 인코더", "category": "검색", "notes": "질문과 문단을 함께 읽는다"}, {"name": ""}],
        "updates": [{"id": "a", "level": 4}, {"id": "b", "level": 4}, {"id": "a", "level": 1}, {"id": "zz", "level": 2}],
    }, ensure_ascii=False) + "\n```"
    r = client.post("/api/concepts/suggest", json={"learningProfile": profile, "question": "리랭킹은 이렇게 동작해요", "answer": "맞아요"})
    assert r.json() == {"additions": [{"name": "크로스 인코더", "category": "검색", "notes": "질문과 문단을 함께 읽는다"}],
                        "updates": [{"id": "a", "level": 4}]}
    llm.reply = "JSON이 아님"
    assert client.post("/api/concepts/suggest", json={"learningProfile": [], "question": "q", "answer": "a"}).json() == {"additions": [], "updates": []}
    assert client.post("/api/concepts/suggest", json={"question": ""}).status_code == 400


def test_delete_removes_the_document_and_its_index(client, tmp_path):
    d = upload(client, make_pdf(tmp_path / "지울것.pdf"))
    assert client.delete(f"/api/documents/{d['id']}").status_code == 200
    assert d["id"] not in [x["id"] for x in client.get("/api/documents", params={"workspace": WS}).json()]
    assert not (config.INDEX_DIR / d["id"]).exists()
    assert client.get(f"/api/documents/{d['id']}/file").status_code == 404


def save(client, upserts=(), deletes=()):
    return client.post("/api/records", json={"upserts": list(upserts), "deletes": list(deletes)})


def test_records_are_kept_on_the_server_in_the_order_they_were_first_saved(client):
    space = {"id": "ws-rec", "name": "운영체제", "instructions": ""}
    answer = {"id": "m2", "role": "assistant", "content": "답 [1]", "sources": [{"documentId": "d", "page": 2, "rects": [[0, 0, 1, 1]]}],
              "pdfNotes": {"1": {"memo": "필기", "status": "confused"}}}
    talk = {"id": "c1", "workspaceId": "ws-rec", "title": "첫 대화", "messages": [{"id": "m1", "role": "user", "content": "질문"}, answer]}
    memory = {"id": "mem1", "workspaceId": "ws-rec", "content": "기억할 것"}
    assert save(client, [{"kind": "workspaces", "body": space}, {"kind": "chats", "body": talk},
                         {"kind": "chats", "body": {**talk, "id": "c2", "title": "둘째 대화"}},
                         {"kind": "memories", "body": memory}]).json() == {"saved": 4, "deleted": 0}
    # saving a chat again replaces it where it was: the note's state changes, the order does not
    answer["pdfNotes"]["1"]["status"] = "understood"
    save(client, [{"kind": "chats", "body": talk}])
    got = client.get("/api/records").json()
    assert set(got) == {"workspaces", "chats", "notes", "tasks", "memories", "cards", "edges", "concepts"}
    mine = [c for c in got["chats"] if c["workspaceId"] == "ws-rec"]
    assert [c["id"] for c in mine] == ["c1", "c2"]
    assert mine[0]["messages"][1]["pdfNotes"]["1"] == {"memo": "필기", "status": "understood"}
    assert memory in got["memories"] and space in got["workspaces"]
    save(client, deletes=[{"kind": "chats", "id": "c2"}])
    assert [c["id"] for c in client.get("/api/records").json()["chats"] if c["workspaceId"] == "ws-rec"] == ["c1"]


def test_deleting_a_workspace_takes_its_records_and_documents_with_it(client, tmp_path):
    save(client, [{"kind": "workspaces", "body": {"id": "ws-gone", "name": "지울 공간"}},
                  {"kind": "tasks", "body": {"id": "t1", "workspaceId": "ws-gone", "text": "할 일"}}])
    d = upload(client, make_pdf(tmp_path / "공간문서.pdf"), workspace="ws-gone")
    assert save(client, deletes=[{"kind": "workspaces", "id": "ws-gone"}]).status_code == 200
    got = client.get("/api/records").json()
    assert "ws-gone" not in [w["id"] for w in got["workspaces"]] and "t1" not in [t["id"] for t in got["tasks"]]
    assert client.get("/api/documents", params={"workspace": "ws-gone"}).json() == []
    assert client.get(f"/api/documents/{d['id']}/file").status_code == 404 and not (config.INDEX_DIR / d["id"]).exists()


def test_malformed_records_are_refused_whole(client):
    before = client.get("/api/records").json()
    good = {"kind": "tasks", "body": {"id": "t-ok", "workspaceId": "ws-rec", "text": "ok"}}
    for bad in ({"kind": "secrets", "body": {"id": "x", "workspaceId": "w"}}, {"kind": "tasks", "body": {"id": "x"}},
                {"kind": "tasks", "body": {"workspaceId": "w"}}, {"kind": "tasks", "body": "text"}):
        assert save(client, [good, bad]).status_code == 400
    assert client.post("/api/records", json={"upserts": "all"}).status_code == 400
    assert client.get("/api/records").json() == before
