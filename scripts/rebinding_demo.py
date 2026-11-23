"""Deterministic, local-only DNS-rebinding comparison for the final lab.

The resolver is injected by the test harness: validation sees the permitted
receiver address first, while the later delivery lookup sees a second local
loopback address that hosts the synthetic report.  No external DNS query or
real internal address is used.  The JSON output is connection-trace evidence,
not a PCAP file.
"""
import argparse
import importlib.util
import json
import socket
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webhook"))
from config import Settings
from transport import Transport


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


internal = load("rebinding_internal", ROOT / "internal/server.py")


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    port = free_port()
    origin = f"http://rebind.lab:{port}"
    url = origin + "/reports/alpha"
    output = {
        "mock_only": True,
        "evidence_kind": "application_connection_trace_not_pcap",
        "description": "registration lookup resolves to allowed local receiver; delivery lookup changes to synthetic internal service",
        "origin": origin,
        "runs": [],
    }
    with tempfile.TemporaryDirectory() as folder:
        server = internal.make_server("127.0.0.2", port, str(Path(folder) / "internal.sqlite3"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for policy in ("baseline", "strict"):
                lookups = []

                def resolver(_host, requested_port, **kwargs):
                    lookups.append("validation_receiver" if len(lookups) == 0 else "delivery_rebound_internal")
                    address = "127.0.0.1" if len(lookups) == 1 else "127.0.0.2"
                    return socket.getaddrinfo(address, requested_port, type=socket.SOCK_STREAM)

                settings = Settings(
                    db_path=str(Path(folder) / (policy + ".sqlite3")),
                    bootstrap_file=str(ROOT / "config/accounts.json"),
                    policy=policy,
                    return_body="full",
                    store_body="full",
                    read_body="full",
                    exceptions=[{"origin": origin, "cidrs": ["127.0.0.1/32"]}],
                )
                transport = Transport(settings, resolver=resolver)
                registered_ips = transport.validate_registration(url)
                run_id = str(uuid.uuid4())
                result = transport.deliver(url, b"{}", run_id)
                observed = json.load(build_opener(ProxyHandler({})).open(
                    f"http://127.0.0.2:{port}/observations?run_id={run_id}", timeout=5))
                reached = observed["internal_api_reached"]
                expected = policy == "baseline"
                if reached != expected:
                    raise AssertionError((policy, reached, result, observed))
                output["runs"].append({
                    "policy": policy,
                    "run_id": run_id,
                    "registration_resolved_ips": registered_ips,
                    "resolver_phases": lookups,
                    "delivery": result,
                    "internal_observation": observed,
                    "internal_api_reached": reached,
                    "report_content_exposed": "REPORT-" + run_id in (result["body"] or ""),
                })
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    output["passed"] = True
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "output": str(path)}))


if __name__ == "__main__":
    main()
