import contextlib
import importlib.util
import io
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webhook"))
from config import Settings, body_view
from server import make_server
from storage import Store
from transport import PolicyError, Transport


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


internal = load("internal_fixture", ROOT / "internal/server.py")
fixture = load("receiver_fixture", ROOT / "fixtures/server.py")


class LabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quiet = contextlib.redirect_stdout(io.StringIO())
        cls.quiet.__enter__()
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        cls.internal = internal.make_server("127.0.0.1", 0, str(cls.folder / "internal.db"))
        cls.report_url = f"http://127.0.0.1:{cls.internal.server_port}/reports/alpha"
        cls.receiver = fixture.make_server("127.0.0.1", 0, str(cls.folder / "receiver.db"), cls.report_url)
        cls.receiver_url = f"http://127.0.0.1:{cls.receiver.server_port}"
        cls.rebound = internal.make_server("127.0.0.2", cls.receiver.server_port, str(cls.folder / "rebound.db"))
        cls.tls = fixture.make_server("127.0.0.1", 0, str(cls.folder / "tls.db"), cls.report_url,
                                      str(ROOT / "tests/certs/server.pem"), str(ROOT / "tests/certs/server-key.pem"))
        cls.tls_url = f"https://localhost:{cls.tls.server_port}"
        for server in (cls.internal, cls.receiver, cls.tls, cls.rebound):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.opener = build_opener(ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        for server in (cls.internal, cls.receiver, cls.tls, cls.rebound):
            server.shutdown()
            server.server_close()
        cls.temp.cleanup()
        cls.quiet.__exit__(None, None, None)

    def settings(self, **kwargs):
        config = dict(db_path=str(self.folder / (str(uuid.uuid4()) + ".db")),
                      bootstrap_file=str(ROOT / "config/accounts.json"), return_body="full",
                      store_body="full", read_body="full", timeout=0.5,
                      ca_file=str(ROOT / "tests/certs/ca.pem"), exceptions=[
                          {"origin": self.receiver_url, "cidrs": ["127.0.0.1/32"]},
                          {"origin": self.tls_url, "cidrs": ["127.0.0.1/32", "::1/128"]}])
        return Settings(**dict(config, **kwargs))

    @contextlib.contextmanager
    def api(self, **settings):
        server = make_server(self.settings(**settings), "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", server.app
        finally:
            server.shutdown()
            server.server_close()
            server.app.close()
            thread.join()

    def call(self, base, path, method="GET", body=None, actor="alice", status=200):
        headers = {"Content-Type": "application/json"}
        if actor:
            headers["Authorization"] = "Bearer lab-" + actor + "-token"
        request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            data = json.load(response)
            self.assertEqual(response.status, status, data)
            return data

    def register(self, base, path="/webhook", run_id=None):
        return self.call(base, "/api/workspaces/alpha/hooks", "POST", {
            "name": "demo", "url": self.receiver_url + path,
            "events": ["model.registered", "issue.updated"], "run_id": run_id}, status=201)

    def reached(self, run_id):
        return self.call(self.report_url.split("/reports/")[0], "/observations?run_id=" + run_id, actor=None)["internal_api_reached"]

    def test_legacy_contract_and_auth(self):
        with self.api() as (base, _):
            self.assertEqual(self.call(base, "/health", actor=None), {"service": "webhook", "ok": True})
            self.assertEqual(self.call(base, "/webhook", "POST", {"event": "connectivity-check"}, actor=None, status=202),
                             {"service": "webhook", "accepted": True})
            self.call(base, "/webhook", "POST", {"event": 1}, status=400)
            self.call(base, "/api/workspaces", actor=None, status=401)
            self.call(base, "/api/workspaces", actor="wrong", status=401)

    def test_workspace_roles_crud_and_run_isolation(self):
        with self.api() as (base, _):
            hook = self.register(base)
            route = "/api/workspaces/alpha/hooks/" + hook["id"]
            for method in ("GET", "DELETE"):
                self.call(base, route, method, actor="bob", status=403)
            for actor in ("viewer", "customer", "bob"):
                self.call(base, "/api/workspaces/alpha/deliveries", actor=actor, status=403)
            self.call(base, "/api/workspaces/alpha/hooks", actor="viewer")
            self.call(base, "/api/workspaces/alpha/hooks", "POST", {}, actor="viewer", status=403)
            self.call(base, "/api/workspaces/alpha/deliveries", actor="operator")
            own = self.call(base, "/api/workspaces", "POST", {"name": "owned"}, actor="customer", status=201)
            self.call(base, "/api/workspaces/" + own["id"] + "/deliveries", actor="customer")
            self.call(base, "/api/workspaces/beta/hooks", "POST", {"run_id": hook["run_id"]}, actor="bob", status=403)
            updated = self.call(base, route, "PUT", {"name": "new", "url": self.receiver_url + "/relative",
                                                     "events": ["issue.updated"]})
            self.assertEqual(updated["name"], "new")
            self.call(base, route, "DELETE")
            self.call(base, route, status=404)

    def test_direct_internal_registration_rejected(self):
        for policy in ("baseline", "reject", "strict"):
            with self.subTest(policy=policy), self.api(policy=policy) as (base, _):
                run_id = str(uuid.uuid4())
                result = self.call(base, "/api/workspaces/alpha/hooks", "POST", {
                    "run_id": run_id, "name": "internal", "url": self.report_url,
                    "events": ["model.registered"]}, status=400)
                self.assertEqual(result["decision"], "denied")
                self.assertFalse(self.reached(run_id))

    def test_both_modes_normal_and_redirect_per_policy(self):
        for policy in ("baseline", "reject", "strict"):
            with self.subTest(policy=policy), self.api(policy=policy) as (base, _):
                for path in ("/webhook", "/redirect"):
                    hook = self.register(base, path)
                    for mode in ("mlflow", "plane"):
                        run_id = str(uuid.uuid4())
                        if mode == "mlflow":
                            data = self.call(base, f'/api/workspaces/alpha/hooks/{hook["id"]}/test', "POST", {"run_id": run_id})
                            result = data["result"]
                        else:
                            queued = self.call(base, "/api/workspaces/alpha/events", "POST", {"run_id": run_id}, status=202)
                            self.assertEqual(len(queued["delivery_ids"]), 1)
                            for _ in range(100):
                                rows = self.call(base, "/api/workspaces/alpha/deliveries?run_id=" + run_id)["deliveries"]
                                if rows and rows[0]["state"] not in ("queued", "sending"):
                                    break
                                time.sleep(0.02)
                            result = rows[0]["result"]
                        reached = path == "/redirect" and policy == "baseline"
                        self.assertEqual(self.reached(run_id), reached)
                        self.assertEqual("REPORT-" + run_id in result["body"], reached)
                        self.assertEqual(result["outcome"], "succeeded" if path == "/webhook" or reached else "blocked")
                    self.call(base, "/api/workspaces/alpha/hooks/" + hook["id"], "DELETE")

    def test_body_controls_do_not_stop_internal_request(self):
        for setting in ("return_body", "store_body", "read_body"):
            for policy in ("mask", "omit", "limit"):
                with self.subTest(setting=setting, body=policy), self.api(policy="baseline", body_limit=8, **{setting: policy}) as (base, app):
                    hook = self.register(base, "/redirect")
                    run_id = str(uuid.uuid4())
                    data = self.call(base, f'/api/workspaces/alpha/hooks/{hook["id"]}/test', "POST", {"run_id": run_id})
                    history = self.call(base, "/api/workspaces/alpha/deliveries?run_id=" + run_id)["deliveries"][0]
                    actual = data["result"]["body"] if setting == "return_body" else history["result"]["body"]
                    self.assertNotIn("REPORT-" + run_id, actual or "")
                    self.assertTrue(self.reached(run_id))
                    if setting == "store_body":
                        stored = app.store.rows("SELECT result FROM deliveries WHERE run_id=?", (run_id,))[0]["result"]
                        self.assertNotIn("REPORT-" + run_id, stored)

    def test_tls_verification_and_host_name(self):
        transport = Transport(self.settings())
        result = transport.deliver(self.tls_url + "/webhook", b"{}", str(uuid.uuid4()))
        self.assertEqual(result["status"], 200)
        self.assertTrue(result["trace"][0]["tls_verified"])
        untrusted = Transport(self.settings(ca_file=None)).deliver(self.tls_url + "/webhook", b"{}", str(uuid.uuid4()))
        self.assertIn("CERTIFICATE_VERIFY_FAILED", untrusted["error"])
        url = self.tls_url.replace("localhost", "wrong.example")
        mismatch = Transport(self.settings(exceptions=[{"origin": url, "cidrs": ["127.0.0.1/32"]}]),
                             resolver=lambda *a, **k: socket.getaddrinfo("127.0.0.1", a[1], type=socket.SOCK_STREAM))
        self.assertIn("CERTIFICATE_VERIFY_FAILED", mismatch.deliver(url, b"{}", str(uuid.uuid4()))["error"])

    def test_dns_changes_and_pinned_connection(self):
        calls = []
        def resolver(host, port, **kwargs):
            calls.append(host)
            return socket.getaddrinfo("127.0.0.1" if len(calls) == 1 else "10.0.0.1", port, **kwargs)
        transport = Transport(self.settings(), resolver=resolver)
        result = transport.deliver(self.receiver_url + "/webhook", b"{}", str(uuid.uuid4()))
        self.assertEqual(result["status"], 200)
        self.assertEqual(len(calls), 1, "connect must not resolve hostname again")
        calls.clear()
        transport.validate_registration(self.receiver_url)
        result = transport.deliver(self.receiver_url + "/webhook", b"{}", str(uuid.uuid4()))
        self.assertEqual(result["outcome"], "blocked")
        self.assertNotIn("connected_ip", result["trace"][0])

    def test_baseline_dns_rebinding_reaches_different_loopback_server(self):
        calls = []
        def resolver(host, port, **kwargs):
            calls.append(host)
            return socket.getaddrinfo("127.0.0.1" if len(calls) == 1 else "127.0.0.2", port, **kwargs)
        transport = Transport(self.settings(policy="baseline"), resolver=resolver)
        transport.validate_registration(self.receiver_url)
        run_id = str(uuid.uuid4())
        result = transport.deliver(self.receiver_url + "/reports/alpha", b"{}", run_id)
        self.assertIn("REPORT-" + run_id, result["body"])
        self.assertEqual(result["trace"][0]["connected_ip"], "127.0.0.2")

    def test_address_denials_mixed_dns_and_exception_scope(self):
        transport = Transport(self.settings())
        for url in ("file:///etc/passwd", "http://a@127.0.0.1/", "http://127.0.0.1/#x", "http://[::ffff:127.0.0.1]/",
                    "http://169.254.169.254/", "http://127.1/", "http://2130706433/", "http://[::1]/", "http://224.0.0.1/",
                    "http://127.0.0.1:0/", "http://[fec0::1]/", self.report_url):
            with self.subTest(url=url), self.assertRaises((PolicyError, OSError)):
                transport.validate_registration(url)
        mixed = Transport(self.settings(), resolver=lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 80))])
        with self.assertRaises(PolicyError):
            mixed.validate_registration("http://public.example/")

    def test_redirect_statuses_relative_loop_downgrade_and_limits(self):
        for code, method in ((301, "GET"), (302, "GET"), (303, "GET"), (307, "POST"), (308, "POST")):
            run_id = str(uuid.uuid4())
            result = Transport(self.settings(policy="baseline")).deliver(self.receiver_url + f"/redirect?status={code}", b"{}", run_id)
            self.assertEqual(result["status"], 200)
            rows = self.call(self.report_url.split("/reports/")[0], "/observations?run_id=" + run_id)["requests"]
            self.assertEqual(rows[0][1], method)
        transport = Transport(self.settings())
        self.assertEqual(transport.deliver(self.receiver_url + "/relative", b"{}", str(uuid.uuid4()))["status"], 200)
        self.assertIn("limit", transport.deliver(self.receiver_url + "/loop", b"{}", str(uuid.uuid4()))["error"])
        self.assertIn("downgrade", transport.deliver(self.tls_url + "/redirect", b"{}", str(uuid.uuid4()))["error"])
        limited = transport.deliver(self.receiver_url + "/large", b"{}", str(uuid.uuid4()))
        self.assertTrue(limited["response_truncated"])
        self.assertLessEqual(len(limited["body"].encode()), 65536)
        failed = transport.deliver(self.receiver_url + "/fail", b"{}", str(uuid.uuid4()))
        self.assertEqual((failed["status"], failed["outcome"]), (503, "failed"))

    def test_restart_keeps_data_and_marks_ambiguous_jobs(self):
        settings = self.settings()
        store = Store(settings.db_path, settings.bootstrap_file)
        store.execute("INSERT INTO deliveries(id,state) VALUES('old','sending')")
        store.execute("INSERT INTO deliveries(id,state) VALUES('pending','queued')")
        reopened = Store(settings.db_path, settings.bootstrap_file)
        states = {row["id"]: row["state"] for row in reopened.rows("SELECT id,state FROM deliveries")}
        self.assertEqual(states, {"old": "interrupted", "pending": "queued"})

    def test_modes_independent_and_invalid_input(self):
        with self.api(enabled_modes=("mlflow",)) as (base, _):
            hook = self.register(base)
            self.call(base, "/api/workspaces/alpha/events", "POST", {}, status=409)
            self.call(base, f'/api/workspaces/alpha/hooks/{hook["id"]}/test', "POST", {"event": []}, status=400)
            self.call(base, "/api/workspaces/alpha/hooks", "POST", {"name": "x", "url": [], "events": ["model.registered"]}, status=400)
            self.call(base, "/api/workspaces/alpha/deliveries?offset=-1", status=400)
        with self.api(enabled_modes=("plane",)) as (base, _):
            hook = self.register(base)
            self.call(base, f'/api/workspaces/alpha/hooks/{hook["id"]}/test', "POST", {}, status=409)


if __name__ == "__main__":
    unittest.main()
