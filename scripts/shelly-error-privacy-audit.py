#!/usr/bin/env python3
"""Audit local Shelly health error fields without displaying their contents."""

from __future__ import annotations

import argparse
import json
import math
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

SAFE_REASONS = frozenset(
    {
        "timeout",
        "http_error",
        "connection_error",
        "connection_refused",
        "invalid_response",
        "read_error",
    }
)


class RejectRedirects(HTTPRedirectHandler):
    """Never follow a response from loopback to another URL."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        """Reject 30x responses instead of contacting a new destination."""

        return None


def parser() -> argparse.ArgumentParser:
    """Describe the bounded, non-interactive, local-only audit."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--health-url", default="http://127.0.0.1:8080/health")
    cli.add_argument("--timeout", default=2.0, type=float)
    cli.add_argument("--dry-run", action="store_true")
    cli.add_argument("--json", action="store_true")
    return cli


def validate_url(raw: str) -> None:
    """Allow only a local HTTP health endpoint, not arbitrary URLs."""

    try:
        url = urlsplit(raw)
        port = url.port
    except ValueError as exc:
        raise ValueError("invalid endpoint") from exc
    if (
        url.scheme != "http"
        or url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or url.username is not None
        or url.password is not None
        or url.path != "/health"
        or url.query
        or url.fragment
        or port is None
        or not 1 <= port <= 65535
    ):
        raise ValueError("only local /health endpoints are allowed")


def audit_snapshot(snapshot: object) -> dict:
    """Check L1 and optional phase errors using a strict fixed allowlist."""

    if not isinstance(snapshot, dict):
        return verdict("fail", "malformed_health")
    phases = snapshot.get("phases")
    if not isinstance(phases, dict):
        return verdict("fail", "missing_phases")
    errors = [snapshot.get("last_error")]
    for phase in ("L1", "L2", "L3"):
        phase_status = phases.get(phase)
        if not isinstance(phase_status, dict):
            return verdict("fail", "malformed_phase")
        if phase_status.get("connectivity") != "not_configured":
            errors.append(phase_status.get("last_error"))
    if any(
        item is not None
        and (not isinstance(item, str) or item not in SAFE_REASONS)
        for item in errors
    ):
        return verdict("fail", "untrusted_error_detail")
    if not any(item is not None for item in errors):
        return verdict("inconclusive", "no_error_observed")
    return verdict("pass", "only_classified_errors")


def verdict(status: str, reason: str) -> dict:
    """Return a sanitized record without echoing original diagnostics."""

    return {
        "status": status,
        "reason": reason,
        "raw_error_exported": False,
        "network_target": "loopback_only",
        "production_changes": 0,
        "telegram_actions": 0,
    }


def audit_live(url: str, timeout: float) -> dict:
    """Fetch one local read-only health snapshot with bounded latency."""

    validate_url(url)
    if not math.isfinite(timeout) or not 0.1 <= timeout <= 5:
        raise ValueError("timeout must be 0.1 to 5 seconds")
    opener = build_opener(ProxyHandler({}), RejectRedirects())
    with opener.open(url, timeout=timeout) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        return verdict("fail", "oversized_health")
    return audit_snapshot(json.loads(raw))


def main() -> int:
    """Return 0 PASS/dry-run, 2 FAIL, or 3 when no error was observed."""

    args = parser().parse_args()
    try:
        validate_url(args.health_url)
        if not math.isfinite(args.timeout) or not 0.1 <= args.timeout <= 5:
            raise ValueError("invalid timeout")
        result = (
            verdict("dry_run", "no_network_calls")
            if args.dry_run
            else audit_live(args.health_url, args.timeout)
        )
    except (ValueError, TypeError, OSError, URLError):
        result = verdict("fail", "health_unavailable_or_invalid")
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['status'].upper()}: {result['reason']}")
    return {"pass": 0, "dry_run": 0, "fail": 2, "inconclusive": 3}[
        result["status"]
    ]


if __name__ == "__main__":
    raise SystemExit(main())
