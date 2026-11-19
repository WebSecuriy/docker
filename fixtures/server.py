"""Kali/VM receiver and fixed-target redirect fixture; all observations are synthetic."""
import argparse
import json
import os
import sqlite3
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        run_id = self.headers.get("X-Run-ID", "")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 <= size <= 16384:
            self.send_error(413)
            return
        body = self.rfile.read(size).decode("utf-8", errors="replace") if size else ""
        if parsed.path == "/health":
            result = {"service": "fixture", "ok": True}
        elif parsed.path == "/observations":
            with sqlite3.connect(self.server.db_path) as db:
                rows = db.execute("SELECT run_id,path,method,body FROM observations WHERE run_id=?", (query.get("run_id", [""])[0],)).fetchall()
            result = {"requests": rows}
        else:
            with sqlite3.connect(self.server.db_path) as db:
                db.execute("INSERT INTO observations VALUES(?,?,?,?)", (run_id, parsed.path, self.command, body))
            print(json.dumps(dict(service="receiver", run_id=run_id, path=parsed.path, method=self.command)), flush=True)
            if parsed.path in ("/redirect", "/loop", "/relative"):
                code = int(query.get("status", ["303"])[0])
                if code not in (301, 302, 303, 307, 308):
                    self.send_error(400)
                    return
                target = self.server.target
                if parsed.path == "/loop":
                    target = "/loop"
                if parsed.path == "/relative":
                    target = "/webhook"
                if parsed.path == "/redirect":
                    target += ("&" if "?" in target else "?") + urlencode({"run_id": run_id})
                self.send_response(code)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if parsed.path == "/fail":
                self.send_error(503)
                return
            result = {"received": True, "run_id": run_id}
            if parsed.path == "/large":
                result["padding"] = "x" * 100000
        raw = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def make_server(host, port, db_path, target, cert=None, key=None):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS observations(run_id TEXT,path TEXT,method TEXT,body TEXT)")
    server = ThreadingHTTPServer((host, port), Handler)
    server.db_path, server.target = db_path, target
    if cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("BIND_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8443")))
    parser.add_argument("--target", default=os.getenv("REPORT_API_URL"), required=not os.getenv("REPORT_API_URL"))
    parser.add_argument("--db", default=os.getenv("FIXTURE_DB_PATH", "data/fixture.sqlite3"))
    parser.add_argument("--cert", default=os.getenv("TLS_CERT"))
    parser.add_argument("--key", default=os.getenv("TLS_KEY"))
    args = parser.parse_args()
    with make_server(args.host, args.port, args.db, args.target, args.cert, args.key) as server:
        server.serve_forever()
