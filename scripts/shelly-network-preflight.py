#!/usr/bin/env python3
"""Diagnose Shelly DNS and TCP reachability with no write operations."""

from __future__ import annotations

import argparse
import errno
import json
import math
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """Parse deterministic, bounded, non-interactive probe options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def target_parts(raw: str) -> tuple[str, int, str]:
    """Parse the configured endpoint without returning private credentials."""

    value = raw.strip()
    parsed = urlsplit(value if "://" in value else f"http://{value}")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("invalid configured endpoint")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("invalid configured endpoint port")
    return parsed.hostname, port, parsed.scheme


def failure_reason(error: OSError) -> str:
    """Classify an operating-system error without disclosing private IPs."""

    if isinstance(error, TimeoutError) or error.errno == errno.ETIMEDOUT:
        return "tcp_timeout"
    if error.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH):
        return "tcp_unreachable"
    if error.errno == errno.ECONNREFUSED:
        return "tcp_refused"
    return "tcp_error"


def result(status: str, reason: str) -> dict:
    """Produce a host-redacted status suitable for nonprivate QA logs."""

    return {
        "status": status,
        "reason": reason,
        "ip_and_credentials_redacted": True,
        "device_power_state_verified": False,
        "device_writes": 0,
        "sqlite_writes": 0,
        "telegram_writes": 0,
    }


def tcp_probe(host: str, port: int, timeout: float) -> dict:
    """Read one bounded DNS result set and test socket connect only."""

    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return result("fail", "dns_failed")
    if not addresses:
        return result("fail", "dns_empty")
    reasons = []
    for family, kind, proto, _, endpoint in addresses[:4]:
        try:
            with socket.socket(family, kind, proto) as sock:
                sock.settimeout(timeout)
                sock.connect(endpoint)
            response = result("pass", "tcp_connected")
            response["dns_candidates"] = len(addresses)
            return response
        except OSError as error:
            reasons.append(failure_reason(error))
    response = result("fail", reasons[0] if reasons else "tcp_error")
    response["dns_candidates"] = len(addresses)
    return response


def run_probe(raw_host: str, timeout: float) -> dict:
    """Classify a reachable TCP endpoint without issuing an HTTP request."""

    if not math.isfinite(timeout) or not 0.1 <= timeout <= 5.0:
        raise ValueError("timeout must be between 0.1 and 5 seconds")
    host, port, scheme = target_parts(raw_host)
    started = time.monotonic()
    response = tcp_probe(host, port, timeout)
    response["scheme"] = scheme
    response["port"] = port
    response["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    response["http_rpc_tested"] = False
    return response


def main() -> int:
    """Return 0 for reachable/dry run or 2 for a blocked diagnostic."""

    args = build_parser().parse_args()
    try:
        if not math.isfinite(args.timeout) or not 0.1 <= args.timeout <= 5:
            raise ValueError("invalid timeout")
        if args.dry_run:
            info = result("dry_run", "no_network_calls")
        else:
            info = run_probe(app.load_config().shelly_host, args.timeout)
    except (ValueError, TypeError, OSError):
        info = result("fail", "invalid_configuration")
    if args.json:
        print(json.dumps(info, ensure_ascii=False, allow_nan=False))
    else:
        print(f"{info['status'].upper()}: {info['reason']}")
    return 2 if info["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
