"""The learner's records, kept on the server: workspaces, chats, notes, tasks, memories,
canvas cards and edges, concepts. The screens load them once and send what changed."""
from fastapi import APIRouter, HTTPException, Request

from lb import store

from . import documents
from .common import id_ok, read_json

router = APIRouter(prefix="/api")
MAX_BATCH = 2000


@router.get("/records")
def records():
    return store.list_records()


def _upsert(item):
    if not isinstance(item, dict):
        return None
    kind, body = item.get("kind"), item.get("body")
    if kind not in store.KINDS or not isinstance(body, dict) or not id_ok(body.get("id")):
        return None
    workspace = body["id"] if kind == "workspaces" else body.get("workspaceId")
    if not id_ok(workspace):
        return None
    return kind, body["id"], workspace, body


def _delete(item):
    if isinstance(item, dict) and item.get("kind") in store.KINDS and id_ok(item.get("id")):
        return item["kind"], item["id"]
    return None


@router.post("/records")
async def save(request: Request):
    body = await read_json(request)
    upserts, deletes = body.get("upserts", []), body.get("deletes", [])
    if not isinstance(upserts, list) or not isinstance(deletes, list) or len(upserts) + len(deletes) > MAX_BATCH:
        raise HTTPException(400, "저장할 기록을 읽을 수 없어요.")
    upserts, deletes = [_upsert(i) for i in upserts], [_delete(i) for i in deletes]
    if None in upserts or None in deletes:
        raise HTTPException(400, "저장할 기록을 읽을 수 없어요.")
    for workspace in store.save_records(upserts, deletes):
        # a workspace that went away takes its documents with it
        for d in store.list_docs(workspace):
            documents.remove(d["id"])
    return {"saved": len(upserts), "deleted": len(deletes)}
