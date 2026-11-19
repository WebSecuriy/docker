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
        )


def body_view(body, policy, limit):
    if policy == "omit":
        return None
    if policy == "mask":
        return "[REDACTED]" if body else ""
    if policy == "limit":
        return body.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    return body
