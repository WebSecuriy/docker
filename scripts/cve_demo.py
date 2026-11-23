"""Exercise only this repository's acknowledgement-gated synthetic CVE lab.

It never contacts a real MLflow/Plane deployment, metadata endpoint, or
non-lab address. The result JSON deliberately separates internal API reach,
response/log read-back, access control, and application-level connection
events (the latter is *not* a packet capture).
"""
import argparse
import json
import os
import ssl
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


def expectation(value):
    if value not in ("true", "false"):
        raise argparse.ArgumentTypeError("expected true or false")
    return value == "true"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.getenv("LAB_BASE_URL", "http://webhook:8000"))
    parser.add_argument("--receiver", default=os.getenv("RECEIVER_URL", "https://receiver:8443"))
    parser.add_argument("--internal", default=os.getenv("REPORT_API_URL", "http://internal:8000/reports/alpha"))
    parser.add_argument("--observer", default=os.getenv("OBSERVER_URL", "http://internal:8000"))
    parser.add_argument("--ca", default=os.getenv("CA_FILE", "/certs/ca.pem"))
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--policy", choices=("baseline", "reject", "strict"), required=True)
    parser.add_argument("--network", choices=("open", "blocked"), default="open")
    parser.add_argument("--mlflow-auth", choices=("none", "required"), required=True)
    parser.add_argument("--expect-mlflow-reach", type=expectation, required=True)
    parser.add_argument("--expect-plane-reach", type=expectation, required=True)
    parser.add_argument("--expect-mlflow-exposure", type=expectation, required=True)
    parser.add_argument("--expect-plane-exposure", type=expectation, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    context = ssl.create_default_context(cafile=args.ca)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
    receiver_redirect = args.receiver + "/redirect?status=302"
    results = {
        "mock_only": True,
        "scenario": args.scenario,
        "policy": args.policy,
        "network": args.network,
        "expected": {
            "mlflow_internal_api_reached": args.expect_mlflow_reach,
            "plane_internal_api_reached": args.expect_plane_reach,
            "mlflow_report_content_exposed": args.expect_mlflow_exposure,
            "plane_report_content_exposed": args.expect_plane_exposure,
            "mlflow_auth": args.mlflow_auth,
        },
        "endpoints": {
            "mlflow_create": "/api/2.0/mlflow/webhooks",
            "mlflow_test": "/api/2.0/mlflow/webhooks/{id}/test",
            "plane_create": "/api/cve/plane/workspaces/alpha/webhooks",
            "plane_logs": "/api/cve/plane/workspaces/alpha/webhook-logs/{id}",
        },
        "mlflow": {},
        "plane": {},
    }

    def call(url, method="GET", body=None, token=None, expected=200):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(url, data=json.dumps(body).encode("utf-8") if body is not None else None,
                          headers=headers, method=method)
        try:
            response = opener.open(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            payload = json.load(response)
            if response.status != expected:
                raise AssertionError((url, response.status, expected, payload))
            return payload

    def observation(run_id):
        return call(args.observer + "/observations?run_id=" + run_id)

    def wait_delivery(run_id, hook_id):
        deadline = time.monotonic() + 30
        while True:
            rows = call(args.base + "/api/workspaces/alpha/deliveries?run_id=" + run_id,
                        token="lab-alice-token")["deliveries"]
            delivery = next((row for row in rows if row["hook_id"] == hook_id), None)
            if delivery and delivery["state"] not in ("queued", "sending"):
                return delivery
            if time.monotonic() >= deadline:
                raise AssertionError("Plane mock worker did not finish")
            time.sleep(0.1)

    mlflow_hook = None
    plane_hook = None
    try:
        # MLflow CVE-2026-64849: a public-looking HTTPS first hop redirects to
        # the synthetic report. The fixed profile can additionally require a
        # Bearer token; that is a local hardening layer, not a claim about the
        # product's default auth configuration.
        mlflow_body = {
            "name": "lab-mlflow-redirect",
            "url": receiver_redirect,
            "events": [{"entity": "REGISTERED_MODEL", "action": "CREATED"}],
            "run_id": str(uuid.uuid4()),
        }
        mlflow_token = "lab-alice-token" if args.mlflow_auth == "required" else None
        if args.mlflow_auth == "required":
            results["mlflow"]["unauthenticated_create"] = call(
                args.base + "/api/2.0/mlflow/webhooks", "POST", mlflow_body, expected=401)
            results["mlflow"]["alpha_member_create"] = call(
                args.base + "/api/2.0/mlflow/webhooks", "POST", mlflow_body,
                token="lab-viewer-token", expected=403)
            results["mlflow"]["other_workspace_admin_create"] = call(
                args.base + "/api/2.0/mlflow/webhooks", "POST", mlflow_body,
                token="lab-bob-token", expected=403)
        direct_run = str(uuid.uuid4())
        direct = dict(mlflow_body, name="direct-internal-negative", url=args.internal, run_id=direct_run)
        results["mlflow"]["direct_internal_registration"] = call(
            args.base + "/api/2.0/mlflow/webhooks", "POST", direct, token=mlflow_token, expected=400)
        assert results["mlflow"]["direct_internal_registration"]["decision"] == "denied"
        created = call(args.base + "/api/2.0/mlflow/webhooks", "POST", mlflow_body, token=mlflow_token)
        hook_id = created["webhook"]["webhook_id"]
        mlflow_hook = hook_id
        mlflow_run = str(uuid.uuid4())
        test_path = args.base + f"/api/2.0/mlflow/webhooks/{hook_id}/test"
        test_body = {"webhook_id": hook_id, "event": {"entity": "REGISTERED_MODEL", "action": "CREATED"},
                     "run_id": mlflow_run}
        if args.mlflow_auth == "required":
            results["mlflow"]["unauthenticated_test"] = call(test_path, "POST", test_body, expected=401)
            results["mlflow"]["alpha_member_test"] = call(
                test_path, "POST", test_body, token="lab-viewer-token", expected=403)
            results["mlflow"]["other_workspace_admin_test"] = call(
                test_path, "POST", test_body, token="lab-bob-token", expected=403)
        tested = call(test_path, "POST", test_body, token=mlflow_token)
        mlflow_observation = observation(mlflow_run)
        mlflow_exposed = "REPORT-" + mlflow_run in (tested["result"]["response_body"] or "")
        assert mlflow_observation["internal_api_reached"] == args.expect_mlflow_reach, tested
        assert mlflow_exposed == args.expect_mlflow_exposure, tested
        results["mlflow"].update({
            "registration": created,
            "run_id": mlflow_run,
            "test_result": tested,
            "internal_observation": mlflow_observation,
            "internal_api_reached": mlflow_observation["internal_api_reached"],
            "report_content_exposed": mlflow_exposed,
            "connection_trace": tested["result"].get("trace", []),
        })

        # Plane CVE-2026-105636: an ADMIN-controlled webhook first points at
        # the lab receiver and the worker follows its 302. The body is then
        # observable through the route-shaped webhook-log endpoint only if the
        # delivery and log policies allow it.
        plane_direct_run = str(uuid.uuid4())
        results["plane"]["direct_internal_registration"] = call(
            args.base + "/api/cve/plane/workspaces/alpha/webhooks", "POST", {
                "name": "direct-internal-negative", "url": args.internal,
                "events": ["issue.updated"], "run_id": plane_direct_run,
            }, token="lab-alice-token", expected=400)
        plane_created = call(args.base + "/api/cve/plane/workspaces/alpha/webhooks", "POST", {
            "name": "lab-plane-redirect", "url": receiver_redirect,
            "events": ["issue.updated"], "run_id": str(uuid.uuid4()),
        }, token="lab-alice-token", expected=201)
        plane_hook = plane_created["id"]
        plane_run = str(uuid.uuid4())
        queued = call(args.base + "/api/workspaces/alpha/events", "POST", {
            "event": "issue.updated", "run_id": plane_run,
        }, token="lab-alice-token", expected=202)
        delivery = wait_delivery(plane_run, plane_hook)
        plane_observation = observation(plane_run)
        logs_path = args.base + f"/api/cve/plane/workspaces/alpha/webhook-logs/{plane_hook}?run_id={plane_run}"
        logs = call(logs_path, token="lab-alice-token")["logs"]
        assert len(logs) == 1, logs
        plane_body = logs[0]["result"]["body"] or ""
        plane_exposed = "REPORT-" + plane_run in plane_body
        assert plane_observation["internal_api_reached"] == args.expect_plane_reach, delivery
        assert plane_exposed == args.expect_plane_exposure, logs
        results["plane"]["same_workspace_member_log_query"] = call(logs_path, token="lab-viewer-token", expected=403)
        results["plane"]["other_workspace_log_query"] = call(logs_path, token="lab-bob-token", expected=403)
        results["plane"].update({
            "registration": plane_created,
            "run_id": plane_run,
            "queued": queued,
            "delivery_log": delivery,
            "webhook_logs": logs,
            "internal_observation": plane_observation,
            "internal_api_reached": plane_observation["internal_api_reached"],
            "report_content_exposed": plane_exposed,
            "connection_trace": delivery["result"].get("trace", []),
        })
        results["passed"] = True
    except Exception as error:
        results["passed"] = False
        results["failure"] = str(error)
        raise
    finally:
        if mlflow_hook:
            try:
                call(args.base + f"/api/workspaces/__cve_mlflow_2026_64849/hooks/{mlflow_hook}", "DELETE",
                     token="lab-alice-token")
            except Exception as error:  # Preserve a primary assertion failure.
                results.setdefault("cleanup_errors", []).append(str(error))
        if plane_hook:
            try:
                call(args.base + f"/api/workspaces/alpha/hooks/{plane_hook}", "DELETE", token="lab-alice-token")
            except Exception as error:  # Preserve a primary assertion failure.
                results.setdefault("cleanup_errors", []).append(str(error))
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "scenario": args.scenario, "output": args.output}))


if __name__ == "__main__":
    main()
