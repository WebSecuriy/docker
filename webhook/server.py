import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class WebhookHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def send_json(self, status, body):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"service": "webhook", "ok": True})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/webhook":
            self.send_json(404, {"error": "not found"})
            return

        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_json(411, {"error": "a valid content length is required"})
            return

        if not 1 <= size <= 4096:
            self.send_json(413, {"error": "body must contain 1 to 4096 bytes"})
            return

        try:
            event = json.loads(self.rfile.read(size))
        except (ValueError, TimeoutError):
            self.send_json(400, {"error": "invalid JSON body"})
            return

        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            self.send_json(400, {"error": "event must be a string"})
            return

        self.send_json(202, {"service": "webhook", "accepted": True})


if __name__ == "__main__":
    with ThreadingHTTPServer(("0.0.0.0", 8000), WebhookHandler) as server:
        server.serve_forever()
