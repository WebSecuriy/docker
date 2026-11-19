import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path, bootstrap_file):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, operator INTEGER);
                CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS members(workspace TEXT, account TEXT, role TEXT,
                    PRIMARY KEY(workspace,account));
                CREATE TABLE IF NOT EXISTS hooks(id TEXT PRIMARY KEY, workspace TEXT, name TEXT, url TEXT, events TEXT);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, workspace TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, workspace TEXT, run_id TEXT,
                    hook_id TEXT, mode TEXT, url TEXT, payload TEXT, policy TEXT, state TEXT,
                    result TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, workspace TEXT, run_id TEXT,
                    event TEXT, detail TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS deliveries_run ON deliveries(workspace,run_id);
                CREATE INDEX IF NOT EXISTS audit_run ON audit(workspace,run_id);
            """)
            # A crash after sending is ambiguous; do not silently send twice on restart.
            db.execute("UPDATE deliveries SET state='interrupted' WHERE state='sending'")
            with open(bootstrap_file, encoding="utf-8") as stream:
                fixtures = json.load(stream)
            for account in fixtures["accounts"]:
                db.execute("INSERT INTO accounts VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET token_hash=excluded.token_hash, operator=excluded.operator",
                           (account["id"], self.hash_token(account["token"]), bool(account.get("operator"))))
            for workspace in fixtures["workspaces"]:
                db.execute("INSERT OR IGNORE INTO workspaces VALUES(?,?)", (workspace["id"], workspace["name"]))
                for account, role in workspace["members"].items():
                    db.execute("INSERT OR IGNORE INTO members VALUES(?,?,?)", (workspace["id"], account, role))

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.connect() as db:
            db.execute(sql, args)

    def audit(self, workspace, run_id, event, **detail):
        self.execute("INSERT INTO audit(workspace,run_id,event,detail) VALUES(?,?,?,?)",
                     (workspace, run_id, event, json.dumps(detail, ensure_ascii=False)))
        print(json.dumps(dict(service="webhook", workspace=workspace, run_id=run_id,
                              event=event, **detail), ensure_ascii=False), flush=True)
