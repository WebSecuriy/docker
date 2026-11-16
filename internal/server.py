import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class InternalHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            status, body = 200, {"service": "internal", "ok": True}
        else:
            status, body = 404, {"error": "not found"}

        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    with ThreadingHTTPServer(("0.0.0.0", 8000), InternalHandler) as server:
        server.serve_forever()
