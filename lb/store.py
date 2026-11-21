"""SQLite store: the documents of each workspace, and the learner's records.

A record is one item the screens keep: a workspace, a chat with its messages (sources, margin
notes and their 헷갈려요 / 이해했어요 state included), a note, a task, a memory, a canvas card or
edge, a concept. Each is stored whole, as the JSON the screens hold, under (kind, id).
"""
import json
import sqlite3
import threading
import time
import uuid

from . import config

_lock = threading.Lock()
KINDS = ("workspaces", "chats", "notes", "tasks", "memories", "cards", "edges", "concepts")
SCHEMA = """
create table if not exists docs(id text primary key, space_id text, name text, status text,
  pages_done int, pages_total int, error text, created_at real, log text);
create table if not exists records(seq integer primary key autoincrement, kind text not null, id text not null,
  workspace_id text, body text not null, updated_at real, unique(kind, id));
create index if not exists records_workspace on records(workspace_id);
"""


def conn():
    config.STORAGE.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.STORAGE / "learnboost.db", check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


_db = None


def db():
    global _db
    if _db is None:
        _db = conn()
        _db.executescript(SCHEMA)
    return _db


def _exec(sql, args=()):
    with _lock:
        cur = db().execute(sql, args)
        db().commit()
        return cur


def _rows(sql, args=()):
    with _lock:
        return [dict(r) for r in db().execute(sql, args).fetchall()]


def new_id():
    return uuid.uuid4().hex[:12]


# ---- documents: each belongs to one workspace (space_id)

def create_doc(space_id, name):
    did = new_id()
    _exec("insert into docs(id, space_id, name, status, pages_done, pages_total, error, created_at, log) "
          "values(?,?,?,?,?,?,?,?,?)", (did, space_id, name, "processing", 0, 0, None, time.time(), "[]"))
    return get_doc(did)


def update_doc(did, **kw):
    sets = ", ".join(f"{k}=?" for k in kw)
    _exec(f"update docs set {sets} where id=?", (*kw.values(), did))


def doc_log(did, text, key=None, done=False):
    """Append a line to a document's processing log; a line with the same `key` as the
    last one replaces it (progress counters update in place)."""
    with _lock:
        r = db().execute("select log from docs where id=?", (did,)).fetchone()
        log = json.loads(r["log"] or "[]") if r else []
        line = {"t": time.time(), "text": text, "key": key, "done": done}
        if key and log and log[-1].get("key") == key:
            line["t"] = log[-1]["t"]
            log[-1] = line
        else:
            if log:
                log[-1]["done"] = True
            log.append(line)
        db().execute("update docs set log=? where id=?", (json.dumps(log, ensure_ascii=False), did))
        db().commit()


def _doc(r):
    r["log"] = json.loads(r.get("log") or "[]")
    return r


def get_doc(did):
    r = _rows("select * from docs where id=?", (did,))
    return _doc(r[0]) if r else None


def list_docs(space_id):
    return [_doc(r) for r in _rows("select * from docs where space_id=? order by created_at", (space_id,))]


def delete_doc(did):
    """Forget a document (its file and index are the caller's to remove)."""
    _exec("delete from docs where id=?", (did,))


# ---- records

def list_records():
    """Every record, grouped by kind, in the order each was first saved."""
    out = {k: [] for k in KINDS}
    for r in _rows("select kind, body from records order by seq"):
        if r["kind"] in out:
            out[r["kind"]].append(json.loads(r["body"]))
    return out


def save_records(upserts, deletes):
    """Apply one batch: `upserts` are (kind, id, workspace_id, body), `deletes` are (kind, id).

    Deleting a workspace deletes everything kept under it. Returns the ids of the workspaces
    that went away, so the caller can remove their documents."""
    now, gone = time.time(), []
    with _lock:
        c = db()
        for kind, rid, workspace_id, body in upserts:
            c.execute("insert into records(kind, id, workspace_id, body, updated_at) values(?,?,?,?,?) "
                      "on conflict(kind, id) do update set workspace_id=excluded.workspace_id, body=excluded.body, "
                      "updated_at=excluded.updated_at",
                      (kind, rid, workspace_id, json.dumps(body, ensure_ascii=False), now))
        for kind, rid in deletes:
            c.execute("delete from records where kind=? and id=?", (kind, rid))
            if kind == "workspaces":
                c.execute("delete from records where workspace_id=?", (rid,))
                gone.append(rid)
        c.commit()
    return gone
