"""Tiny JSON-document store on SQLite: one table per collection, FHIR-shaped JSON in `doc`."""
import sqlite3, json, uuid, os
DB = os.path.join(os.path.dirname(__file__), "copilot.db")
COLLECTIONS = ["users", "records", "schedules", "doctors", "emergencies", "donors", "alerts", "chats", "auth_sessions"]

def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def init():
    with conn() as c:
        for t in COLLECTIONS:
            c.execute(f"CREATE TABLE IF NOT EXISTS {t} (id TEXT PRIMARY KEY, owner TEXT, doc TEXT)")

def put(table, doc, owner=None):
    doc.setdefault("id", str(uuid.uuid4()))
    with conn() as c:
        if owner is None:
            r = c.execute(f"SELECT owner FROM {table} WHERE id=?", (doc["id"],)).fetchone()
            owner = r["owner"] if r else None
        c.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)", (doc["id"], owner, json.dumps(doc)))
    return doc

def get(table, id_):
    with conn() as c:
        r = c.execute(f"SELECT doc FROM {table} WHERE id=?", (id_,)).fetchone()
    return json.loads(r["doc"]) if r else None

def all_(table, owner=None):
    with conn() as c:
        q, a = f"SELECT doc FROM {table}", ()
        if owner: q += " WHERE owner=?"; a = (owner,)
        return [json.loads(r["doc"]) for r in c.execute(q, a)]

def delete(table, id_, owner):
    with conn() as c:
        result = c.execute(f"DELETE FROM {table} WHERE id=? AND owner=?", (id_, owner))
    return result.rowcount > 0
