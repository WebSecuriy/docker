"""Explicit redirect loop and IP-pinned HTTP(S), without environment proxies."""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit


class PolicyError(ValueError):
    pass


class Transport:
    def __init__(self, settings, resolver=socket.getaddrinfo):
        self.settings = settings
        self.resolver = resolver
        self.context = ssl.create_default_context()
        if settings.ca_file:
            self.context.load_verify_locations(cafile=settings.ca_file)
        self.rules = [(self.origin(rule["origin"]), [ipaddress.ip_network(cidr) for cidr in rule["cidrs"]])
                      for rule in settings.exceptions]

    def parse(self, url):
        if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in url):
            raise PolicyError("invalid URL")
        try:
            parsed = urlsplit(url)
            port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
            host = parsed.hostname
            if (parsed.scheme not in ("http", "https") or not host or parsed.username is not None
                    or parsed.password is not None or parsed.fragment or "\\" in url or "%" in host
                    or not 1 <= port <= 65535):
                raise ValueError()
            host = host.encode("idna").decode("ascii").lower()
        except (ValueError, UnicodeError) as error:
            raise PolicyError("invalid URL authority/scheme") from error
        if parsed.scheme == "http" and not self.settings.allow_http:
            raise PolicyError("HTTP disabled; use HTTPS")
        return parsed, host, port

    def resolve(self, url, validate):
        parsed, host, port = self.parse(url)
        addresses = list(dict.fromkeys(info[4][0] for info in self.resolver(host, port, type=socket.SOCK_STREAM)))
        if not addresses:
            raise PolicyError("DNS returned no addresses")
        if validate:
            for address in addresses:
                ip = ipaddress.ip_address(address)
                public = ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified
                                               or (ip.version == 6 and ip.is_site_local))
                if ip.version == 6 and ip.ipv4_mapped:
                    mapped = ip.ipv4_mapped
                    public = mapped.is_global and not (mapped.is_multicast or mapped.is_reserved)
                excepted = any(
                    origin == (parsed.scheme, host, port) and any(ip in cidr for cidr in networks)
                    for origin, networks in self.rules
                )
                if not public and not excepted:
                    raise PolicyError(f"non-public address denied: {address}")
        return parsed, host, port, addresses

    def origin(self, url):
        parsed, host, port = self.parse(url)
        return parsed.scheme, host, port

    def validate_registration(self, url):
        return self.resolve(url, True)[3]

    def validate_plane_v121_registration(self, url):
        """Reproduce only Plane v1.2.1's registration check for the lab.

        The vulnerable serializer rejected loopback addresses but accepted other
        non-public ranges.  The caller must additionally enforce the lab's
        fixed synthetic target; this method must never be used by the regular
        webhook-management API.
        """
        _parsed, _host, _port, addresses = self.resolve(url, False)
        for address in addresses:
            if ipaddress.ip_address(address).is_loopback:
                raise PolicyError(f"loopback address denied by Plane v1.2.1 model: {address}")
        return addresses

    def same_url(self, left, right):
        """Compare canonical scheme, host, port, path and query for lab allowlists."""
        left_parsed, left_host, left_port = self.parse(left)
        right_parsed, right_host, right_port = self.parse(right)
        return (left_parsed.scheme, left_host, left_port, left_parsed.path or "/", left_parsed.query) == (
            right_parsed.scheme, right_host, right_port, right_parsed.path or "/", right_parsed.query
        )

    def request(self, url, method, payload, headers, trace):
        # Baseline deliberately performs fresh, unchecked DNS after registration.
        checked = self.settings.policy in ("strict", "reject")
        parsed, host, port, addresses = self.resolve(url, checked)
        address = addresses[0]
        trace.update(url=url, resolved_ips=addresses, selected_ip=address,
                     decision="validated_and_pinned" if checked else "baseline_unchecked")
        connection = http.client.HTTPConnection(host, port, timeout=self.settings.timeout)
        sock = None
        try:
            # Numeric address only: there is no second hostname lookup at connect time.
            trace["attempted_ips"] = []
            for index, address in enumerate(addresses):
                trace["attempted_ips"].append(address)
                family = socket.AF_INET6 if ":" in address else socket.AF_INET
                sock = socket.socket(family, socket.SOCK_STREAM)
                sock.settimeout(self.settings.timeout)
                try:
                    sock.connect((address, port))
                    trace["selected_ip"] = address
                    break
                except OSError:
                    sock.close()
                    if index == len(addresses) - 1:
                        raise
            peer = sock.getpeername()[0]
            trace["connected_ip"] = peer
            if ipaddress.ip_address(peer) != ipaddress.ip_address(address):
                raise PolicyError("connected peer differs from selected IP")
            if parsed.scheme == "https":
                sock = self.context.wrap_socket(sock, server_hostname=host)
                trace["tls_verified"] = True
            connection.sock = sock
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            authority = f"[{host}]" if ":" in host else host
            headers = dict(headers, Host=f"{authority}:{port}")
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read(self.settings.max_response + 1)
            trace["status"] = response.status
            return response.status, response.getheader("Location"), raw[:self.settings.max_response], len(raw) > self.settings.max_response
        finally:
            connection.close()
            if sock is not None:
                sock.close()

    def deliver(self, url, payload, run_id):
        started = time.monotonic()
        traces = []
        result = dict(status=None, body="", error=None, outcome="failed", response_truncated=False)
        method = "POST"
        headers = {"Content-Type": "application/json", "X-Run-ID": run_id,
                   "User-Agent": "YechukLab-Mock/1.0", "Connection": "close"}
        try:
            for hop in range(self.settings.max_redirects + 1):
                trace = {"hop": hop, "url": url}
                traces.append(trace)
                status, location, raw, truncated = self.request(url, method, payload, headers, trace)
                result.update(status=status, response_truncated=truncated)
                if status in (301, 302, 303, 307, 308) and location:
                    trace["redirect"] = location
                    if self.settings.policy == "reject":
                        raise PolicyError("redirect denied")
                    if hop == self.settings.max_redirects:
                        raise PolicyError("redirect limit exceeded")
                    next_url = urljoin(url, location)
                    if self.settings.policy == "strict" and urlsplit(url).scheme == "https" and urlsplit(next_url).scheme != "https":
                        raise PolicyError("HTTPS downgrade denied")
                    url = next_url
                    if status == 303 or (status in (301, 302) and method == "POST"):
                        method, payload = "GET", None
                    continue
                result.update(body=raw.decode("utf-8", errors="replace"),
                              outcome="succeeded" if 200 <= status < 300 else "failed")
                break
        except (OSError, ValueError, http.client.HTTPException) as error:
            result["error"] = str(error)
            if isinstance(error, PolicyError):
                result["outcome"] = "blocked"
            if traces:
                traces[-1]["error"] = str(error)
                traces[-1]["decision"] = "denied" if isinstance(error, PolicyError) else "connection_error"
        result.update(trace=traces, latency_ms=round((time.monotonic() - started) * 1000, 2))
        return result
