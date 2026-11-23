import json
import os
import sqlite3
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def record(db_path, run_id, method, path, source_ip):
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO observations(run_id,method,path,source_ip) VALUES(?,?,?,?)",
                   (run_id, method, path, source_ip))
    print(json.dumps(dict(service="internal", run_id=run_id, event="report_reached", method=method,
                          path=path, source_ip=source_ip)), flush=True)


class InternalHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/health":
            status, body = 200, {"service": "internal", "ok": True}
        elif parsed.path == "/observations":
            try:
                run_id = str(uuid.UUID(query.get("run_id", [""])[0]))
                with sqlite3.connect(self.server.db_path) as db:
                    rows = db.execute("SELECT run_id,method,path,created,source_ip FROM observations WHERE run_id=?", (run_id,)).fetchall()
                status, body = 200, {"run_id": run_id, "internal_api_reached": bool(rows), "requests": rows}
            except ValueError:
                status, body = 400, {"error": "run_id must be a UUID"}
        elif parsed.path.startswith("/reports/"):
            try:
                run_id = str(uuid.UUID(self.headers.get("X-Run-ID") or query.get("run_id", [""])[0]))
                customer = parsed.path.removeprefix("/reports/")
                if customer not in ("alpha", "beta"):
                    raise ValueError()
                record(self.server.db_path, run_id, self.command, parsed.path, self.client_address[0])
                status, body = 200, {"report_marker": "REPORT-" + run_id, "run_id": run_id,
                                     "fictional": True, "customer": customer, "model_operations":
                                     {"active_models": 3, "last_model": "demand-demo-v1"},
                                     "contract_summary": {"plan": "demo-enterprise", "annual_credits": 12000}}
            except ValueError:
                status, body = 400, {"error": "valid customer and run_id required"}
        else:
            status, body = 404, {"error": "not found"}

        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 <= size <= 16384:
                self.send_error(413)
                return
            self.rfile.read(size)
        except ValueError:
            self.send_error(400)
            return
        self.do_GET()


def make_server(host="0.0.0.0", port=8000, db_path="data/internal.sqlite3"):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS observations(run_id TEXT,method TEXT,path TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,source_ip TEXT)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(observations)")}
        if "source_ip" not in columns:
            db.execute("ALTER TABLE observations ADD COLUMN source_ip TEXT")
    server = ThreadingHTTPServer((host, port), InternalHandler)
    server.db_path = db_path
    return server


if __name__ == "__main__":
    with make_server(os.getenv("BIND_HOST", "0.0.0.0"), int(os.getenv("PORT", "8000")),
                     os.getenv("INTERNAL_DB_PATH", "data/internal.sqlite3")) as server:
        server.serve_forever()
