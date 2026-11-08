"""Read-only EM-50 release gate: distinguish QA PASS from rollout approval."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yasno_cache import read_verified_cache  # noqa: E402
from yasno_handoff import validate_target  # noqa: E402
from yasno_schedule import parse_schedule  # noqa: E402


class DenyRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow loopback HTTP redirects to another host."""

    def redirect_request(self, request, file, code, message, headers, url):
        """Reject a redirect without opening its destination."""

        raise ValueError("health endpoint redirects are not permitted")


PENDING = (
    "private_recipient_visible_send_and_edit",
    "real_LXC_systemd_activation_review",
    "explicit_release_and_rollback_authorization",
)


def local_json(url: str) -> dict:
    """Read bounded HTTP JSON only from localhost, without credentials."""

    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"/health", "/ready"}
    ):
        raise ValueError("health URL must be a local HTTP endpoint")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), DenyRedirect()
    )
    with opener.open(url, timeout=5) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("health response too large")
    return json.loads(raw)


def health_check(health_url: str, ready_url: str, now: float) -> str:
    """Check actual installed service is responsive and reading Shelly."""

    health = local_json(health_url)
    ready = local_json(ready_url)
    age = now - float(health["last_success"])
    if health["connectivity"] != "online" or not 0 <= age < 120:
        raise ValueError("Shelly reading is offline or stale")
    if not isinstance(ready, dict) or ready.get("ready") is not True:
        raise ValueError("readiness endpoint did not confirm ready")
    return (
        f"runtime={health.get('version', 'unknown')}; "
        f"reading_age_s={age:.1f}; ready=PASS"
    )


def sqlite_check(path: Path) -> str:
    """Verify production SQLite via mode=ro, without exposing message IDs."""

    if not path.is_file():
        raise ValueError("database missing")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
        count = db.execute("SELECT count(*) FROM measurements").fetchone()[0]
        keys = {"telegram_status_message_id", "telegram_message_id"}
        stored = any(
            db.execute(
                "SELECT 1 FROM meta WHERE key=? AND value!=''",
                (key,),
            ).fetchone()
            for key in keys
        )
    if integrity != "ok" or count <= 0 or not stored:
        raise ValueError("SQLite integrity or status metadata failed")
    return f"integrity=ok; measurements={count}; status_id_saved=true"


def backup_check(directory: Path, now: float, max_age_hours: float) -> str:
    """Validate the newest backup checksum and its age without extraction."""

    files = sorted(
        directory.glob("voltage-*.sqlite3.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise ValueError("no SQLite backups")
    backup = files[0]
    checksum = Path(str(backup) + ".sha256")
    if any(p.is_symlink() for p in (backup, checksum)):
        raise ValueError("backup symlinks are not accepted")
    line = checksum.read_text(encoding="utf-8").strip()
    expected, filename = line.split(maxsplit=1)
    if filename.strip().lstrip("*") != str(backup):
        raise ValueError("backup checksum refers to another file")
    with backup.open("rb") as content:
        actual = hashlib.file_digest(content, "sha256").hexdigest()
    age_h = (now - backup.stat().st_mtime) / 3600
    if actual != expected or not 0 <= age_h <= max_age_hours:
        raise ValueError("backup checksum or age failed")
    return f"sha256=ok; latest_age_hours={age_h:.1f}"


def reader_cache_check(path: Path, group: str, gid: int) -> str:
    """Validate owner/group read-only handoff and fresh Kyiv forecast."""

    validate_target(path, gid)
    data, captured = read_verified_cache(str(path))
    status = parse_schedule(data, group, datetime.now(timezone.utc), captured)
    return f"group={status.group}; day={status.day}; source=YASNO"


def version_check(root: Path) -> str:
    """Ensure this remains a prerelease build, never a stable promotion."""

    candidate = (root / "VERSION").read_text(encoding="utf-8").strip()
    release = (root / "RELEASE_VERSION").read_text(encoding="utf-8").strip()
    if candidate != "0.5.14-hw" or candidate == release:
        raise ValueError("expected unpublished 0.5.14-hw candidate")
    return f"candidate={candidate}; stable_marker_unchanged=true"


def check(action) -> dict[str, str]:
    """Run one isolated check with a clear non-secret status."""

    try:
        detail = action()
        return {"status": "PASS", "detail": detail}
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        IndexError,
        sqlite3.DatabaseError,
    ) as exc:
        return {
            "status": "FAIL",
            "detail": type(exc).__name__,
        }


def inspect(args: argparse.Namespace) -> dict:
    """Produce a release report that cannot pretend manual acceptance."""

    now = time.time()
    checks = {
        "source_candidate": check(lambda: version_check(Path(args.root))),
        "live_health": check(
            lambda: health_check(args.health_url, args.ready_url, now),
        ),
        "sqlite_readonly": check(lambda: sqlite_check(Path(args.db))),
        "latest_backup_sha256": check(
            lambda: backup_check(
                Path(args.backup_dir), now, args.max_age_hours
            ),
        ),
        "recipient_cache": check(
            lambda: reader_cache_check(
                Path(args.cache), args.group, args.recipient_gid
            ),
        ),
    }
    return {
        "automated_pass": all(
            result["status"] == "PASS" for result in checks.values()
        ),
        "checks": checks,
        "manual_pending": list(PENDING),
        "release_authorized": False,
    }


def main() -> int:
    """Show machine-readable PASS/FAIL without changing production files."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--health-url", default="http://127.0.0.1:8080/health")
    parser.add_argument("--ready-url", default="http://127.0.0.1:8080/ready")
    parser.add_argument(
        "--db", default="/var/lib/shelly-voltage/voltage.sqlite3"
    )
    parser.add_argument("--backup-dir", default="/var/backups/shelly-voltage")
    parser.add_argument(
        "--cache", default="/var/lib/shelly-yasno-reader/yasno.json"
    )
    parser.add_argument("--group", default="10.1")
    parser.add_argument("--recipient-gid", type=int, default=10001)
    parser.add_argument("--max-age-hours", type=float, default=26)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = inspect(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        for name, item in result["checks"].items():
            print(f"{item['status']} {name}: {item['detail']}")
        print(
            "BLOCKED release: manual recipient, systemd and approval pending"
        )
    return 2 if result["automated_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
