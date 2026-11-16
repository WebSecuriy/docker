import argparse
import json
from socket import gaierror
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect", choices=("reachable", "blocked"), default="reachable")
    expected = parser.parse_args().expect
    opener = build_opener(ProxyHandler({}), NoRedirect())

    try:
        with opener.open("http://internal:8000/health", timeout=3) as response:
            data = json.load(response)
            if response.status != 200 or data != {"service": "internal", "ok": True}:
                raise SystemExit(f"FAIL: unexpected internal response: {data!r}")
    except HTTPError as error:
        raise SystemExit(f"FAIL: connected but received HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, OSError):
            kind = "dns-error" if isinstance(reason, gaierror) else "connection-error"
            if expected == "blocked":
                print(f"PASS: internal service unreachable ({kind}: {reason})")
                return
        raise SystemExit(f"FAIL: could not reach internal service: {reason}") from error

    if expected == "blocked":
        raise SystemExit("FAIL: internal service is still reachable")
    print(f"PASS: internal service reached: {json.dumps(data)}")


if __name__ == "__main__":
    main()
