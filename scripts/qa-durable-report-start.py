#!/usr/bin/env python3
"""Review and launch the exact isolated synthetic Report Worker profile."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "mode_start", Path(__file__).with_name("qa-durable-mode-start.py")
)
assert SPEC and SPEC.loader
START = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(START)
OP = START.OP
TX = START.TX
CP = OP.load("qa-compose-provenance")
READY = OP.load("qa-report-runtime-acceptance")
RECEIPT = OP.load("qa-report-launch-receipt")
PROBE = {
    "probe.py": (
        "013c82431b17d149c8ca9720f2d914bb2084c2640cc09d2b2525c612bef8b3ef"
    ),
    "test_app.py": (
        "6c809ca51d8ede939713f9feac72b40beebb502984bd72a7bebfcc43aa4ecffd"
    ),
}


def source_plan(root, mode):
    """Define the closed source Compose profile with no external input."""
    return {
        "services": {
            "shelly-voltage": {
                "container_name": "svt-qa-" + root.name,
                "image": START.IMAGE,
                "pull_policy": "never",
                "restart": "no",
                "network_mode": "none",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges"],
                "user": "10001:10001",
                "ipc": "private",
                "entrypoint": ["python"],
                "command": ["/qa/probe.py"],
                "env_file": [".env"],
                "environment": {
                    "QA_STAGE": "paused" if mode == "paused" else "resumed",
                    "SVT_APP_PATH": "/app/app.py",
                    "PYTHONPATH": "/app:/qa",
                    "SVT_WRITER_FENCE_DIR": "/run/svt-fence",
                },
                "volumes": [
                    f"{root / name}:{target}:{access}"
                    for name, target, access in (
                        ("data", "/data", "rw"),
                        ("fence", "/run/svt-fence", "rw"),
                        ("probe", "/qa", "ro"),
                    )
                ],
            }
        }
    }


def check_probe(root):
    """Require the exact two protected probe files and no extra imports."""
    path = root / "probe"
    TX.protected(path, directory=True)
    if {p.name for p in path.iterdir()} != set(PROBE):
        raise ValueError("unexpected probe files")
    for name, expected in PROBE.items():
        file = path / name
        TX.protected(file)
        if (
            file.stat().st_size > 2 * 1024 * 1024
            or TX.sha(file.read_bytes()) != expected
        ):
            raise ValueError("probe bytes changed")


def review_plan(root, mode):
    """Verify exact source and resolved Compose before checking Docker."""
    if OP.config(root) != f"SCHEDULED_REPORTS_MODE={mode}\n".encode():
        raise ValueError("unexpected fixture configuration")
    check_probe(root)
    raw = START.read_bytes(root / "compose.yaml")
    if json.loads(raw, object_pairs_hook=OP.PLAN.unique_object) != source_plan(
        root, mode
    ):
        raise ValueError("unexpected Compose source")
    plan = CP.resolved_plan(root)
    service = CP.expected_service(root, START.IMAGE)
    service.update({"entrypoint": ["python"], "command": ["/qa/probe.py"]})
    service["environment"] = {
        **source_plan(root, mode)["services"]["shelly-voltage"]["environment"],
        "SCHEDULED_REPORTS_MODE": mode,
    }
    service["volumes"].append(
        {
            "type": "bind",
            "source": str(root / "probe"),
            "target": "/qa",
            "read_only": True,
            "bind": {"create_host_path": True},
        }
    )
    project = CP.re.sub(r"[^a-z0-9_-]", "", root.name.lower())
    if plan != {"name": project, "services": {"shelly-voltage": service}}:
        raise ValueError("unexpected resolved Compose profile")
    return service, {
        "compose_sha256": TX.sha(raw),
        "resolved_sha256": START.hash_object(plan),
        "probe_sha256": START.hash_object(PROBE),
    }


def check_runtime(root, info, mode):
    """Check actual process, effective environment, and offline mounts."""
    service, evidence = review_plan(root, mode)
    host, cfg = info["HostConfig"], info["Config"]
    if (
        info["Image"] != START.IMAGE
        or cfg["Image"] != START.IMAGE
        or host["NetworkMode"] != "none"
        or host["RestartPolicy"]["Name"] != "no"
    ):
        raise ValueError("unexpected runtime image or network")
    START.BOUND.check_host(info)
    image = START.BOUND.PAIR.inspect_image(START.IMAGE)
    defaults = dict(
        image["Config"],
        Entrypoint=service["entrypoint"],
        Cmd=service["command"],
    )
    CP.check_runtime(info, {"Config": defaults}, service)
    actual = sorted(
        (m["Destination"], m["Source"], m["Type"], m["RW"])
        for m in info["Mounts"]
    )
    expected = sorted(
        (v["target"], v["source"], "bind", not v.get("read_only", False))
        for v in service["volumes"]
    )
    if actual != expected:
        raise ValueError("runtime mounts changed")
    return evidence


def validate(root, info, record):
    """Qualify only a never-started fixture, not an arbitrary process."""
    if (
        not OP.PLAN.HEX.fullmatch(info["Id"])
        or info["State"]["Running"] is not False
        or info["State"]["StartedAt"] != "0001-01-01T00:00:00Z"
    ):
        raise ValueError("unreviewed stopped runtime")
    return check_runtime(root, info, record["target"])


def binding(root, token, journal_sha):
    """Share the existing immutable ARM/claim implementation."""
    return START.binding(root, token, journal_sha, validator=validate)


def launch_evidence(root, token, anchor):
    """Verify exact consumed claim and released journal before observing."""
    arm = START.read_bytes(START.path_for(root, token, "arm"))
    if TX.sha(arm) != anchor:
        raise ValueError("arm anchor mismatch")
    record = json.loads(arm, object_pairs_hook=OP.PLAN.unique_object)
    claim = json.loads(START.read_bytes(START.path_for(root, token, "claim")))
    if claim != {
        "arm_sha256": anchor,
        "container_id": record["container_id"],
        "deployment_authorized": False,
    }:
        raise ValueError("claim mismatch")
    journal = OP.read(root, token)
    original = dict(journal, phase="configured")
    original_raw = (json.dumps(original, sort_keys=True) + "\n").encode()
    if (
        journal["phase"] != "released"
        or TX.sha(original_raw) != record["journal_sha256"]
        or OP.pending(root, token) is not None
    ):
        raise ValueError("released journal mismatch")
    return record, journal


def check_observed(root, info, record, journal):
    """Compare immutable runtime and the original historical prefix."""
    evidence = check_runtime(root, info, journal["target"])
    for key in ("Config", "HostConfig", "Mounts"):
        if (
            START.hash_object(info[key])
            != record[
                key.lower().replace("hostconfig", "host_config") + "_sha256"
            ]
        ):
            raise ValueError("runtime identity drift")
    if any(record[key] != value for key, value in evidence.items()):
        raise ValueError("runtime evidence drift")
    old = journal["baseline"]
    history = TX.history_snapshot(root, old["last_id"])
    if any(history[k] != old[k] for k in ("device", "inode", "prefix_sha256")):
        raise ValueError("historical prefix changed")
    if (
        journal["target"] == "paused"
        and history["metadata_sha256"] != old["metadata_sha256"]
    ):
        raise ValueError("paused metadata changed")
    return history


def observe(root, token, anchor):
    """Bind fresh readiness and preserved history to this exact launch."""
    record, journal = launch_evidence(root, token, anchor)
    name = "svt-qa-" + root.name
    info = TX.inspect_qa_container(root, name)
    state = info["State"]
    if (
        info["Id"] != record["container_id"]
        or state["Running"] is not True
        or state["StartedAt"] == "0001-01-01T00:00:00Z"
    ):
        raise ValueError("claimed runtime not running")
    RECEIPT.check_launch(START, root, token, anchor, info)
    check_observed(root, info, record, journal)
    history = TX.history_snapshot(root)
    old = journal["baseline"]
    READY.fetch_ready(info["Id"])
    phases = READY.live_phase_evidence(root, journal, state["StartedAt"])
    final = TX.inspect_qa_container(root, name)
    if (
        final["Id"] != info["Id"]
        or final["State"]["StartedAt"] != state["StartedAt"]
        or not final["State"]["Running"]
    ):
        raise ValueError("runtime changed during observation")
    result = {
        "container_id": info["Id"],
        "started_at": state["StartedAt"],
        "baseline_rows": old["rows"],
        "rows": history["rows"],
        "phases": phases,
        "qa_runtime_verified": True,
    }
    return persist_acceptance(root, token, anchor, result, history)


def persist_acceptance(root, token, anchor, result, history):
    """Persist and report the immutable first accepted observation."""
    acceptance = RECEIPT.accept(START, root, token, anchor, result, history)
    return {
        **result,
        "acceptance_sha256": TX.sha(
            START.read_bytes(START.path_for(root, token, "acceptance"))
        ),
        "accepted_rows": acceptance["history"]["rows"],
    }


def record_launch(root, token, anchor, identity):
    """Persist the launch result before releasing operator gates."""
    return RECEIPT.save_launch(START, root, token, anchor, identity)


def arguments():
    """Parse explicit isolated review, start, and observation actions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("action", choices=("arm", "start", "observe"))
    return parser.parse_args()


def execute(args):
    """Select the fixed runtime profile under existing protected gates."""
    TX.require_qa(args.root)
    if args.action == "start":
        return START.start(
            args.root,
            args.token,
            args.anchor,
            bind=binding,
            on_started=record_launch,
        )
    with TX.exclusive(args.root, observation=args.action == "observe"):
        if args.action == "arm":
            return START.arm(args.root, args.token, args.anchor, bind=binding)
        return observe(args.root, args.token, args.anchor)


def main():
    """Return only nonsecret QA evidence; never authorize production."""
    try:
        result = execute(arguments())
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        subprocess.SubprocessError,
        TX.WriterFenceError,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(
        json.dumps(
            {
                **result,
                "status": "PASS_QA_REPORT_START_ONLY",
                "deployment_authorized": False,
                "startup_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
