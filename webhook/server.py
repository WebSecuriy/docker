"""YechukLab educational mock; not an MLflow or Plane distribution."""
import dataclasses
import json
import os
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from config import Settings, body_view
from storage import Store
from transport import Transport

EVENTS = {"model.registered", "issue.updated"}
MLFLOW_CVE_WORKSPACE = "__cve_mlflow_2026_64849"
MLFLOW_CVE_EVENT = {"entity": "REGISTERED_MODEL", "action": "CREATED"}


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Application:
    def __init__(self, settings):
        self.settings = settings
        self.transport = Transport(settings)
        self.store = Store(settings.db_path, settings.bootstrap_file)
        if settings.mlflow_cve_demo:
            self.store.execute("INSERT OR IGNORE INTO workspaces VALUES(?,?)",
                               (MLFLOW_CVE_WORKSPACE, "Synthetic MLflow CVE demo"))
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.worker = threading.Thread(target=self.work, name="single-delivery-worker", daemon=True)
        self.worker.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        self.worker.join(timeout=(self.settings.max_redirects + 1) * self.settings.timeout * 3 + 2)

    def work(self):
        while not self.stop.is_set():
            try:
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute("SELECT * FROM deliveries WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
                    if row:
                        db.execute("UPDATE deliveries SET state='sending' WHERE id=?", (row["id"],))
                if row:
                    self.send(dict(row))
                    continue
            except Exception as error:
                print(json.dumps({"service": "worker", "error": str(error)}), flush=True)
            self.wake.wait(0.2)
            self.wake.clear()

    def require(self, account, workspace, admin=True, run_id=None):
        members = self.store.rows("SELECT role FROM members WHERE workspace=? AND account=?", (workspace, account["id"]))
        if not account["operator"] and (not members or (admin and members[0]["role"] != "admin")):
            self.store.audit(workspace, run_id, "access_denied", actor=account["id"])
            raise ApiError(403, "workspace permission denied")
        if not self.store.rows("SELECT id FROM workspaces WHERE id=?", (workspace,)):
            raise ApiError(404, "workspace not found")

    def run(self, workspace, value=None):
        try:
            run_id = str(uuid.UUID(value)) if value is not None else str(uuid.uuid4())
        except (ValueError, TypeError, AttributeError) as error:
            raise ApiError(400, "run_id must be a UUID") from error
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO runs(id,workspace) VALUES(?,?)", (run_id, workspace))
            if db.execute("SELECT workspace FROM runs WHERE id=?", (run_id,)).fetchone()[0] != workspace:
                raise ApiError(403, "run belongs to another workspace")
        return run_id

    def hook(self, workspace, hook_id):
        rows = self.store.rows("SELECT * FROM hooks WHERE id=? AND workspace=?", (hook_id, workspace))
        if not rows:
            raise ApiError(404, "webhook not found")
        hook = rows[0]
        hook["events"] = json.loads(hook["events"])
        return hook

    def mlflow_cve_event(self, event):
        if not isinstance(event, dict) or event.get("entity") != MLFLOW_CVE_EVENT["entity"] or event.get("action") != MLFLOW_CVE_EVENT["action"]:
            raise ApiError(400, "CVE demo accepts only REGISTERED_MODEL/CREATED")
        return "model.registered"

    def mlflow_cve_create(self, body):
        """Minimal, route-shaped model of the vulnerable MLflow create call."""
        run_id = self.run(MLFLOW_CVE_WORKSPACE, body.get("run_id"))
        name, url, events = body.get("name"), body.get("url"), body.get("events")
        if not isinstance(name, str) or not 1 <= len(name) <= 100 or not isinstance(events, list) or not events:
            raise ApiError(400, "name, url and events are required")
        try:
            if self.transport.parse(url)[0].scheme != "https":
                raise ValueError("MLflow CVE demo requires an HTTPS first hop")
            normalized_events = [self.mlflow_cve_event(event) for event in events]
            ips = self.transport.validate_registration(url)
        except (ValueError, OSError) as error:
            self.store.audit(MLFLOW_CVE_WORKSPACE, run_id, "mlflow_cve_registration", url=url,
                             decision="denied", error=str(error))
            return 400, {"run_id": run_id, "error": str(error), "decision": "denied",
                         "mock_profile": "mlflow-cve-2026-64849"}
        hook_id = str(uuid.uuid4())
        self.store.execute("INSERT INTO hooks VALUES(?,?,?,?,?)", (hook_id, MLFLOW_CVE_WORKSPACE, name, url,
                                                                     json.dumps(normalized_events)))
        self.store.audit(MLFLOW_CVE_WORKSPACE, run_id, "mlflow_cve_registration", hook_id=hook_id, url=url,
                         resolved_ips=ips, decision="allowed", validator="initial_url_only")
        return 200, {"run_id": run_id, "mock_profile": "mlflow-cve-2026-64849",
                     "webhook": {"webhook_id": hook_id, "name": name, "url": url,
                                 "events": events, "status": "ACTIVE"}}

    def mlflow_cve_test(self, hook_id, body):
        """Minimal, route-shaped model of the vulnerable unauthenticated /test call."""
        hook = self.hook(MLFLOW_CVE_WORKSPACE, hook_id)
        if "webhook_id" in body and body["webhook_id"] != hook_id:
            raise ApiError(400, "webhook_id does not match path")
        event = self.mlflow_cve_event(body.get("event"))
        if event not in hook["events"]:
            raise ApiError(400, "event is not subscribed")
        run_id = self.run(MLFLOW_CVE_WORKSPACE, body.get("run_id"))
        # The affected MLflow delivery path checked the *original* URL again
        # before delivery, then made a separate (unpinned) connection.  Keeping
        # these as two operations makes the redirect/DNS TOCTOU visible here.
        try:
            original_ips = self.transport.validate_registration(hook["url"])
        except (ValueError, OSError) as error:
            self.store.audit(MLFLOW_CVE_WORKSPACE, run_id, "mlflow_cve_test_validation", hook_id=hook_id,
                             url=hook["url"], decision="denied", error=str(error))
            return 400, {"run_id": run_id, "mock_profile": "mlflow-cve-2026-64849",
                         "result": {"success": False, "response_status": None, "response_body": "",
                                    "outcome": "blocked", "error": str(error)}}
        self.store.audit(MLFLOW_CVE_WORKSPACE, run_id, "mlflow_cve_test_validation", hook_id=hook_id,
                         url=hook["url"], resolved_ips=original_ips, decision="allowed")
        delivery = self.enqueue(MLFLOW_CVE_WORKSPACE, run_id, hook, "mlflow-cve-2026-64849", event)
        result = self.send(delivery)
        response_body = body_view(result["body"], self.settings.return_body, self.settings.body_limit)
        self.expose(MLFLOW_CVE_WORKSPACE, run_id, response_body, "mlflow_cve_test")
        return 200, {"run_id": run_id, "delivery_id": delivery["id"], "mock_profile": "mlflow-cve-2026-64849",
                     "result": {"success": result["outcome"] == "succeeded", "response_status": result["status"],
                                "response_body": response_body, "outcome": result["outcome"], "error": result["error"]}}

    def plane_cve_create(self, workspace, body):
        """Constrained model of Plane <=1.2.1's direct-private-URL registration flaw."""
        run_id = self.run(workspace, body.get("run_id"))
        name, url, events = body.get("name"), body.get("url"), body.get("events")
        if not isinstance(name, str) or not 1 <= len(name) <= 100 or not isinstance(events, list) or not events or any(
                not isinstance(event, str) or event not in EVENTS for event in events):
            raise ApiError(400, "name and subscriptions (model.registered, issue.updated) required")
        try:
            if self.settings.policy == "baseline":
                ips = self.transport.validate_plane_v121_registration(url)
                if not self.transport.same_url(url, self.settings.cve_plane_target):
                    raise ValueError("CVE demo target is not the configured synthetic internal report URL")
                validator = "plane_v1_2_1_loopback_only"
            else:
                ips = self.transport.validate_registration(url)
                validator = "standard_public_destination"
        except (ValueError, OSError) as error:
            self.store.audit(workspace, run_id, "plane_cve_registration", url=url, decision="denied",
                             validator="plane_v1_2_1_loopback_only" if self.settings.policy == "baseline" else "standard_public_destination",
                             error=str(error))
            return 400, {"run_id": run_id, "error": str(error), "decision": "denied",
                         "mock_profile": "plane-cve-2026-30242"}
        hook_id = str(uuid.uuid4())
        self.store.execute("INSERT INTO hooks VALUES(?,?,?,?,?)", (hook_id, workspace, name, url, json.dumps(events)))
        self.store.audit(workspace, run_id, "plane_cve_registration", hook_id=hook_id, url=url, resolved_ips=ips,
                         decision="allowed", validator=validator, lab_target_only=True)
        return 201, dict(self.hook(workspace, hook_id), run_id=run_id, mock_profile="plane-cve-2026-30242",
                         validator=validator)

    def enqueue(self, workspace, run_id, hook, mode, event):
        delivery_id = str(uuid.uuid4())
        payload = json.dumps(dict(run_id=run_id, event=event, workspace_id=workspace,
                                  webhook_id=hook["id"], data={"fictional": True, "company": "예측랩",
                                  "model": "demand-demo-v1", "issue": "DEMO-1", "status": "updated"}), ensure_ascii=False)
        self.store.execute("INSERT INTO deliveries(id,workspace,run_id,hook_id,mode,url,payload,policy,state) VALUES(?,?,?,?,?,?,?,?,?)",
                           (delivery_id, workspace, run_id, hook["id"], mode, hook["url"], payload,
                             json.dumps(dataclasses.asdict(self.settings)), "sending" if mode.startswith("mlflow") else "queued"))
        self.store.audit(workspace, run_id, "delivery_created", delivery_id=delivery_id,
                         mode=mode, initial_url=hook["url"], policy=self.settings.policy,
                         body_policies={"return": self.settings.return_body, "store": self.settings.store_body, "read": self.settings.read_body})
        return self.store.rows("SELECT * FROM deliveries WHERE id=?", (delivery_id,))[0]

    def send(self, delivery):
        settings = Settings(**json.loads(delivery["policy"]))
        try:
            result = Transport(settings).deliver(delivery["url"], delivery["payload"].encode(), delivery["run_id"])
        except (OSError, ValueError) as error:
            result = dict(status=None, body="", error=str(error), outcome="failed", response_truncated=False, trace=[], latency_ms=0)
        saved = dict(result, body=body_view(result["body"], settings.store_body, settings.body_limit), body_storage=settings.store_body)
        self.store.execute("UPDATE deliveries SET state=?,result=? WHERE id=?", (result["outcome"], json.dumps(saved), delivery["id"]))
        self.store.audit(delivery["workspace"], delivery["run_id"], "delivery_finished",
                         delivery_id=delivery["id"], status=result["status"], outcome=result["outcome"],
                         latency_ms=result["latency_ms"], trace=result["trace"], error=result["error"])
        return result

    def expose(self, workspace, run_id, body, channel):
        self.store.audit(workspace, run_id, "body_exposure", channel=channel,
                         report_content_exposed=("REPORT-" + run_id) in (body or ""),
                         internal_api_reached="unknown; check internal observations")

    def history(self, workspace, run_id=None, offset=0):
        query, args = "SELECT * FROM deliveries WHERE workspace=?", [workspace]
        if run_id:
            query += " AND run_id=?"
            args.append(run_id)
        rows = self.store.rows(query + " ORDER BY rowid LIMIT 100 OFFSET ?", (*args, offset))
        for row in rows:
            row.pop("payload")
            row["policy"] = json.loads(row["policy"])["policy"]
            if row["result"]:
                result = json.loads(row["result"])
                original = result["body"]
                result["body"] = body_view(original, self.settings.read_body, self.settings.body_limit) if original is not None else None
                result["body_read"] = self.settings.read_body
                row["result"] = result
                self.expose(workspace, row["run_id"], result["body"], "history")
        return rows


class WebhookHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    @property
    def app(self):
        return self.server.app

    def log_message(self, *_args):
        pass

    def send_json(self, status, body):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def read_json(self, limit=16384):
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError as error:
            raise ApiError(411, "a valid content length is required") from error
        if not 1 <= size <= limit:
            raise ApiError(413, f"body must contain 1 to {limit} bytes")
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(400, "Transfer-Encoding is unsupported")
        try:
            raw = self.rfile.read(size)
            body = json.loads(raw)
            if len(raw) != size or not isinstance(body, dict):
                raise ValueError()
            return body
        except (ValueError, TimeoutError) as error:
            raise ApiError(400, "invalid JSON body") from error

    def authenticate(self):
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise ApiError(401, "Bearer token required")
        rows = self.app.store.rows("SELECT id,operator FROM accounts WHERE token_hash=?", (Store.hash_token(auth[7:]),))
        if not rows:
            raise ApiError(401, "invalid token")
        return rows[0]

    def dispatch(self):
        parsed = urlsplit(self.path)
        parts = parsed.path.strip("/").split("/")
        if self.command == "GET" and parsed.path == "/health":
            return 200, {"service": "webhook", "ok": True}
        if self.command == "POST" and parsed.path == "/webhook":
            event = self.read_json(4096)
            if not isinstance(event.get("event"), str):
                raise ApiError(400, "event must be a string")
            self.app.store.audit(None, None, "connectivity_received", event_name=event["event"][:100])
            return 202, {"service": "webhook", "accepted": True}
        if not parts or parts[0] != "api":
            raise ApiError(404, "not found")
        # This route is deliberately unauthenticated only under the explicit,
        # acknowledgement-gated local MLflow CVE profile.  It remains absent in
        # the normal mock service.
        if parts[:4] == ["api", "2.0", "mlflow", "webhooks"]:
            if not self.app.settings.mlflow_cve_demo:
                raise ApiError(404, "not found")
            if self.command == "POST" and len(parts) == 4:
                return self.app.mlflow_cve_create(self.read_json())
            if self.command == "POST" and len(parts) == 6 and parts[5] == "test":
                return self.app.mlflow_cve_test(parts[4], self.read_json())
            raise ApiError(404, "not found")
        account = self.authenticate()
        if parts == ["api", "me"] and self.command == "GET":
            return 200, dict(account, memberships=self.app.store.rows("SELECT workspace,role FROM members WHERE account=?", (account["id"],)))
        if parts == ["api", "workspaces"]:
            if self.command == "GET":
                rows = self.app.store.rows("SELECT * FROM workspaces") if account["operator"] else self.app.store.rows(
                    "SELECT w.* FROM workspaces w JOIN members m ON m.workspace=w.id WHERE m.account=?", (account["id"],))
                return 200, {"workspaces": rows}
            if self.command == "POST":
                name = self.read_json().get("name")
                if not isinstance(name, str) or not 1 <= len(name) <= 100:
                    raise ApiError(400, "name must contain 1 to 100 characters")
                workspace = str(uuid.uuid4())
                with self.app.store.connect() as db:
                    db.execute("INSERT INTO workspaces VALUES(?,?)", (workspace, name))
                    db.execute("INSERT INTO members VALUES(?,?,?)", (workspace, account["id"], "admin"))
                return 201, {"id": workspace, "name": name, "role": "admin"}
        # The Plane CVE route is authenticated and ADMIN-scoped, unlike the
        # MLflow profile above.  It is intentionally separate from the safe
        # generic registration route so the normal API never accepts private
        # destinations merely because a demo profile is enabled.
        if parts[:4] == ["api", "cve", "plane", "workspaces"]:
            if not self.app.settings.plane_cve_demo:
                raise ApiError(404, "not found")
            if self.command != "POST" or len(parts) != 6 or parts[5] != "webhooks":
                raise ApiError(404, "not found")
            workspace = parts[4]
            self.app.require(account, workspace, admin=True)
            return self.app.plane_cve_create(workspace, self.read_json())
        if len(parts) < 4 or parts[:2] != ["api", "workspaces"]:
            raise ApiError(404, "not found")
        workspace, resource = parts[2:4]
        query = parse_qs(parsed.query)
        requested_run = query.get("run_id", [None])[0]
        if requested_run:
            try:
                requested_run = str(uuid.UUID(requested_run))
            except ValueError as error:
                raise ApiError(400, "run_id must be a UUID") from error
        self.app.require(account, workspace, admin=not (self.command == "GET" and resource == "hooks"), run_id=requested_run)
        if resource == "hooks":
            if self.command == "GET":
                if len(parts) == 5:
                    return 200, self.app.hook(workspace, parts[4])
                if len(parts) == 4:
                    rows = self.app.store.rows("SELECT id FROM hooks WHERE workspace=? LIMIT 100", (workspace,))
                    return 200, {"hooks": [self.app.hook(workspace, row["id"]) for row in rows]}
            if self.command == "DELETE" and len(parts) == 5:
                self.app.hook(workspace, parts[4])
                self.app.store.execute("DELETE FROM hooks WHERE id=? AND workspace=?", (parts[4], workspace))
                return 200, {"deleted": True}
            if (self.command == "POST" and len(parts) == 4) or (self.command == "PUT" and len(parts) == 5):
                body = self.read_json()
                run_id = self.app.run(workspace, body.get("run_id"))
                name, url, events = body.get("name"), body.get("url"), body.get("events")
                if not isinstance(name, str) or not 1 <= len(name) <= 100 or not isinstance(events, list) or not events or any(not isinstance(e, str) or e not in EVENTS for e in events):
                    raise ApiError(400, "name and subscriptions (model.registered, issue.updated) required")
                try:
                    ips = self.app.transport.validate_registration(url)
                except (ValueError, OSError) as error:
                    self.app.store.audit(workspace, run_id, "registration", url=url, decision="denied", error=str(error))
                    return 400, {"run_id": run_id, "error": str(error), "decision": "denied"}
                hook_id = str(uuid.uuid4()) if len(parts) == 4 else self.app.hook(workspace, parts[4])["id"]
                self.app.store.execute("INSERT INTO hooks VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,events=excluded.events",
                                       (hook_id, workspace, name, url, json.dumps(events)))
                self.app.store.audit(workspace, run_id, "registration", hook_id=hook_id, url=url, resolved_ips=ips, decision="allowed")
                return 201 if self.command == "POST" else 200, dict(self.app.hook(workspace, hook_id), run_id=run_id)
            if self.command == "POST" and len(parts) == 6 and parts[5] == "test":
                if "mlflow" not in self.app.settings.enabled_modes:
                    raise ApiError(409, "mlflow mode disabled")
                body = self.read_json()
                hook = self.app.hook(workspace, parts[4])
                event = body.get("event", "model.registered")
                if not isinstance(event, str) or event not in hook["events"]:
                    raise ApiError(400, "event is not subscribed")
                run_id = self.app.run(workspace, body.get("run_id"))
                delivery = self.app.enqueue(workspace, run_id, hook, "mlflow", event)
                result = self.app.send(delivery)
                result["body"] = body_view(result["body"], self.app.settings.return_body, self.app.settings.body_limit)
                result["body_return"] = self.app.settings.return_body
                self.app.expose(workspace, run_id, result["body"], "mlflow_test")
                return 200, {"run_id": run_id, "delivery_id": delivery["id"], "mode": "mlflow_mock", "result": result}
        if resource == "events" and len(parts) == 4 and self.command == "POST":
            if "plane" not in self.app.settings.enabled_modes:
                raise ApiError(409, "plane mode disabled")
            body = self.read_json()
            event = body.get("event", "issue.updated")
            if not isinstance(event, str) or event not in EVENTS:
                raise ApiError(400, "unsupported event")
            run_id = self.app.run(workspace, body.get("run_id"))
            hooks = self.app.store.rows("SELECT id FROM hooks WHERE workspace=?", (workspace,))
            deliveries = []
            for row in hooks:
                hook = self.app.hook(workspace, row["id"])
                if event in hook["events"]:
                    deliveries.append(self.app.enqueue(workspace, run_id, hook, "plane", event)["id"])
            self.app.wake.set()
            return 202, {"run_id": run_id, "mode": "plane_mock", "delivery_ids": deliveries}
        if resource in ("deliveries", "runs") and self.command == "GET":
            if resource == "runs":
                if len(parts) != 5:
                    raise ApiError(404, "run id required")
                try:
                    requested_run = str(uuid.UUID(parts[4]))
                except ValueError as error:
                    raise ApiError(400, "run_id must be a UUID") from error
                if not self.app.store.rows("SELECT id FROM runs WHERE id=? AND workspace=?", (requested_run, workspace)):
                    raise ApiError(404, "run not found")
            try:
                offset = int(query.get("offset", ["0"])[0])
                if offset < 0:
                    raise ValueError()
            except ValueError as error:
                raise ApiError(400, "offset must be a nonnegative integer") from error
            self.app.store.audit(workspace, requested_run, "logs_queried", actor=account["id"], offset=offset)
            response = {"deliveries": self.app.history(workspace, requested_run, offset), "offset": offset, "limit": 100}
            if resource == "runs":
                response["audit"] = self.app.store.rows("SELECT * FROM audit WHERE workspace=? AND run_id=? ORDER BY id LIMIT 1000", (workspace, requested_run))
            return 200, response
        raise ApiError(404, "not found")

    def handle_api(self):
        try:
            status, body = self.dispatch()
        except ApiError as error:
            status, body = error.status, {"error": error.message}
        except (ValueError, TypeError) as error:
            status, body = 400, {"error": str(error)}
        except (sqlite3.Error, OSError):
            status, body = 503, {"error": "service unavailable"}
        self.send_json(status, body)

    do_GET = do_POST = do_PUT = do_DELETE = handle_api


def make_server(settings, host="0.0.0.0", port=8000):
    app = Application(settings)
    try:
        server = ThreadingHTTPServer((host, port), WebhookHandler)
        server.app = app
        return server
    except BaseException:
        app.close()
        raise


if __name__ == "__main__":
    with make_server(Settings.from_env(), os.getenv("BIND_HOST", "0.0.0.0"), int(os.getenv("PORT", "8000"))) as server:
        try:
            server.serve_forever()
        finally:
            server.app.close()
