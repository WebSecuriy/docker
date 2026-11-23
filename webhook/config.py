"""Configuration shared by Docker and bare Python deployments (stdlib only)."""
import json
import ipaddress
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    db_path: str = "data/webhook.sqlite3"
    bootstrap_file: str = "config/accounts.json"
    policy: str = "strict"
    enabled_modes: tuple = ("mlflow", "plane")
    return_body: str = "mask"
    store_body: str = "mask"
    read_body: str = "mask"
    body_limit: int = 1024
    max_response: int = 65536
    timeout: float = 3
    max_redirects: int = 5
    ca_file: str | None = None
    allow_http: bool = True
    exceptions: list = field(default_factory=list)
    # Disabled by default.  These profiles are deliberately constrained to the
    # local synthetic CVE lab; they are not production compatibility switches.
    cve_demo_mode: str = "none"
    cve_demo_ack: str = ""
    # The CVE profiles below are version-labelled educational behaviours, not
    # product binaries.  Keeping them explicit lets the lab compare the
    # vulnerable and fixed delivery paths without installing either product.
    mlflow_version_profile: str = "v3.13.0-vulnerable"
    mlflow_cve_auth_required: bool = False
    plane_version_profile: str = "v1.3.1-vulnerable"
    cve_plane_origin: str | None = None
    plane_log_filter: str = "none"

    def __post_init__(self):
        if self.policy not in ("baseline", "reject", "strict"):
            raise ValueError("POLICY must be baseline, reject or strict")
        if not set(self.enabled_modes) <= {"mlflow", "plane"} or not self.enabled_modes:
            raise ValueError("ENABLED_MODES must contain mlflow and/or plane")
        for value in (self.return_body, self.store_body, self.read_body):
            if value not in ("full", "limit", "mask", "omit"):
                raise ValueError("body policy must be full, limit, mask or omit")
        if not 1 <= self.body_limit <= self.max_response <= 1048576:
            raise ValueError("require 1 <= BODY_LIMIT <= MAX_RESPONSE_BYTES <= 1048576")
        if not 0 < self.timeout <= 30 or not 0 <= self.max_redirects <= 10:
            raise ValueError("invalid timeout or redirect limit")
        if not isinstance(self.exceptions, list):
            raise ValueError("exceptions must be a list")
        for rule in self.exceptions:
            if not isinstance(rule, dict) or not isinstance(rule.get("origin"), str) or not isinstance(rule.get("cidrs"), list) or not rule["cidrs"]:
                raise ValueError("exception requires origin and nonempty cidrs")
            for cidr in rule["cidrs"]:
                ipaddress.ip_network(cidr)
        if self.cve_demo_mode not in ("none", "mlflow-2026-64849", "plane-2026-105636", "all"):
            raise ValueError("CVE_DEMO_MODE must be none, mlflow-2026-64849, plane-2026-105636 or all")
        if self.cve_demo_mode != "none" and self.cve_demo_ack != "local-lab-only":
            raise ValueError("CVE demo requires CVE_DEMO_ACK=local-lab-only")
        if self.mlflow_version_profile not in ("v3.13.0-vulnerable", "v3.15.0-fixed"):
            raise ValueError("MLFLOW_VERSION_PROFILE must be v3.13.0-vulnerable or v3.15.0-fixed")
        if self.plane_version_profile not in ("v1.3.1-vulnerable", "v1.4.0-fixed"):
            raise ValueError("PLANE_VERSION_PROFILE must be v1.3.1-vulnerable or v1.4.0-fixed")
        if self.plane_log_filter not in ("none", "redact-sensitive", "metadata-only"):
            raise ValueError("PLANE_LOG_FILTER must be none, redact-sensitive or metadata-only")
        if self.plane_cve_demo and not self.cve_plane_origin:
            raise ValueError("Plane CVE demo requires CVE_DEMO_PLANE_ORIGIN")

    @property
    def mlflow_cve_demo(self):
        return self.cve_demo_mode in ("mlflow-2026-64849", "all")

    @property
    def plane_cve_demo(self):
        return self.cve_demo_mode in ("plane-2026-105636", "all")

    @classmethod
    def from_env(cls):
        exception_file = os.getenv("URL_EXCEPTIONS_FILE", "config/url-exceptions.json")
        with open(exception_file, encoding="utf-8") as stream:
            exceptions = json.load(stream)
        return cls(
            db_path=os.getenv("DB_PATH", "data/webhook.sqlite3"),
            bootstrap_file=os.getenv("BOOTSTRAP_FILE", "config/accounts.json"),
            policy=os.getenv("POLICY", "strict"),
            enabled_modes=tuple(os.getenv("ENABLED_MODES", "mlflow,plane").split(",")),
            return_body=os.getenv("RETURN_BODY", "mask"),
            store_body=os.getenv("STORE_BODY", "mask"),
            read_body=os.getenv("READ_BODY", "mask"),
            body_limit=int(os.getenv("BODY_LIMIT", "1024")),
            max_response=int(os.getenv("MAX_RESPONSE_BYTES", "65536")),
            timeout=float(os.getenv("DELIVERY_TIMEOUT", "3")),
            max_redirects=int(os.getenv("MAX_REDIRECTS", "5")),
            ca_file=os.getenv("CA_FILE") or None,
            allow_http=os.getenv("ALLOW_HTTP", "true").lower() == "true",
            exceptions=exceptions,
            cve_demo_mode=os.getenv("CVE_DEMO_MODE", "none"),
            cve_demo_ack=os.getenv("CVE_DEMO_ACK", ""),
            mlflow_version_profile=os.getenv("MLFLOW_VERSION_PROFILE", "v3.13.0-vulnerable"),
            mlflow_cve_auth_required=os.getenv("MLFLOW_CVE_AUTH_REQUIRED", "false").lower() == "true",
            plane_version_profile=os.getenv("PLANE_VERSION_PROFILE", "v1.3.1-vulnerable"),
            cve_plane_origin=os.getenv("CVE_DEMO_PLANE_ORIGIN") or None,
            plane_log_filter=os.getenv("PLANE_LOG_FILTER", "none"),
        )


def body_view(body, policy, limit):
    if policy == "omit":
        return None
    if policy == "mask":
        return "[REDACTED]" if body else ""
    if policy == "limit":
        return body.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    return body


def filter_plane_log(body, policy):
    """Apply a deliberately narrow, deterministic filter to synthetic logs.

    This is a lab control for the *log exposure* part of the Plane scenario;
    it is not a general data-loss-prevention engine.  It runs before the
    generic store/read body policies, so a redacted value cannot be recovered
    later through a different log endpoint.
    """
    if not body or policy == "none":
        return body
    if policy == "metadata-only":
        return json.dumps({"filtered": True, "response_bytes": len(body.encode("utf-8"))}, ensure_ascii=False)
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return "[REDACTED-PLANE-LOG]"
    if isinstance(payload, dict):
        for key in ("report_marker", "contract_summary", "model_operations"):
            if key in payload:
                payload[key] = "[REDACTED]"
        payload["log_filtered"] = True
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "[REDACTED-PLANE-LOG]"
