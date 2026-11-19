"""Same black-box cases for Docker, Kali and separate Linux VMs; stdlib only."""
import argparse
import json
import os
import ssl
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.getenv("LAB_BASE_URL", "http://webhook:8000"))
    parser.add_argument("--receiver", default=os.getenv("RECEIVER_URL", "https://receiver:8443"))
    parser.add_argument("--internal", default=os.getenv("REPORT_API_URL", "http://internal:8000/reports/alpha"))
    parser.add_argument("--observer", default=os.getenv("OBSERVER_URL", "http://internal:8000"))
    parser.add_argument("--ca", default=os.getenv("CA_FILE", "/certs/ca.pem"))
    parser.add_argument("--policy", choices=("baseline", "reject", "strict"), required=True)
    parser.add_argument("--network", choices=("open", "blocked"), default="open")
    parser.add_argument("--return-body", default="full")
    parser.add_argument("--store-body", default="full")
    parser.add_argument("--read-body", default="full")
    parser.add_argument("--body-limit", type=int, default=1024)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    context = ssl.create_default_context(cafile=args.ca)
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))

    def call(url, method="GET", body=None, token=None, expected=200):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            response = opener.open(request, timeout=45)
        except HTTPError as error:
            response = error
        with response:
            data = json.load(response)
            assert response.status == expected, (url, response.status, expected, data)
            return data

    customer = os.getenv("CUSTOMER_TOKEN", "lab-customer-token")
    bob = os.getenv("OTHER_WORKSPACE_TOKEN", "lab-bob-token")
    results = {"policy": args.policy, "network": args.network, "body": {
        "return": args.return_body, "store": args.store_body, "read": args.read_body}, "cases": []}
    call(args.base + "/health")
    call(args.base + "/webhook", "POST", {"event": "connectivity-check"}, expected=202)
    workspace = call(args.base + "/api/workspaces", "POST", {"name": "matrix-" + str(uuid.uuid4())}, customer, 201)["id"]
    api = args.base + "/api/workspaces/" + workspace
    try:
        for mode in ("mlflow", "plane"):
            for kind, path in (("normal", "/webhook"), ("redirect", "/redirect")):
                run_id = str(uuid.uuid4())
                hook = call(api + "/hooks", "POST", {"run_id": run_id, "name": kind,
                    "url": args.receiver + path, "events": ["model.registered", "issue.updated"]}, customer, 201)
                try:
                    if mode == "mlflow":
                        response = call(api + "/hooks/" + hook["id"] + "/test", "POST", {"run_id": run_id}, customer)
                        result = response["result"]
                    else:
                        response = call(api + "/events", "POST", {"run_id": run_id, "event": "issue.updated"}, customer, 202)
                        deadline = time.monotonic() + 40
                        while True:
                            history = call(api + "/deliveries?run_id=" + run_id, token=customer)["deliveries"]
                            if history and history[0]["state"] not in ("queued", "sending"):
                                result = history[0]["result"]
                                break
                            assert time.monotonic() < deadline, "worker timeout"
                            time.sleep(0.1)
                    observation = call(args.observer + "/observations?run_id=" + run_id)
                    received = call(args.receiver + "/observations?run_id=" + run_id)
                    reached = observation["internal_api_reached"]
                    expected_reach = kind == "redirect" and args.policy == "baseline" and args.network == "open"
                    assert reached == expected_reach, (kind, mode, observation)
                    exposed = "REPORT-" + run_id in (result["body"] or "")
                    policies = [args.return_body] if mode == "mlflow" else [args.store_body, args.read_body]
                    marker_end = len('{"report_marker": "') + len("REPORT-" + run_id)
                    marker_visible = all(p == "full" or (p == "limit" and args.body_limit >= marker_end) for p in policies)
                    assert exposed == (expected_reach and marker_visible), (kind, mode, result)
                    if kind == "normal":
                        assert result["outcome"] == "succeeded" and result["status"] == 200, result
                        assert len(received["requests"]) == 1, received
                    elif args.policy != "baseline":
                        assert result["outcome"] == "blocked", result
                    elif args.network == "blocked":
                        assert result["outcome"] == "failed", result
                    call(api + "/deliveries?run_id=" + run_id, token=bob, expected=403)
                    call(api + "/hooks", token=bob, expected=403)
                    audit = call(api + "/runs/" + run_id, token=customer)
                    assert audit["deliveries"][0]["policy"] == args.policy
                    results["cases"].append(dict(mode=mode, kind=kind, run_id=run_id, internal_api_reached=reached,
                        report_content_exposed=exposed, result=result, internal=observation, receiver=received, server=audit))
                finally:
                    call(api + "/hooks/" + hook["id"], "DELETE", token=customer)
        run_id = str(uuid.uuid4())
        direct = call(api + "/hooks", "POST", {"run_id": run_id, "name": "direct-internal",
            "url": args.internal, "events": ["model.registered"]}, customer, 400)
        assert direct["decision"] == "denied", direct
        results["direct_internal"] = dict(run_id=run_id, result=direct,
            observation=call(args.observer + "/observations?run_id=" + run_id))
        assert not results["direct_internal"]["observation"]["internal_api_reached"]
        results["normal_success_rate"] = sum(c["result"]["outcome"] == "succeeded" for c in results["cases"] if c["kind"] == "normal") / 2
        results["passed"] = True
    finally:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "policy": args.policy, "network": args.network,
                      "normal_success_rate": results["normal_success_rate"], "output": args.output}))


if __name__ == "__main__":
    main()
