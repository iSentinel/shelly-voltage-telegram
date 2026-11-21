"""Compare an existing QA receipt to the journal; never stage or start."""

from __future__ import annotations

import importlib.util
import os
import socket
from pathlib import Path

from cutover_journal import JournalBlocked, digest, validate_identity

ROOT = Path(__file__).resolve().parent
STAGE = Path("/var/lib/svt-qa-verified-pair-20261119")


def helper(filename: str):
    """Load an adjacent existing policy checker."""
    spec = importlib.util.spec_from_file_location(
        "svt_qa_receipt_" + filename.replace("-", "_"), ROOT / filename
    )
    if spec is None or spec.loader is None:
        raise JournalBlocked("RECEIPT_CHECKER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_identity(record: dict, identity: dict) -> None:
    """Bind all immutable receipt fields to one transaction."""
    validate_identity(identity)
    fields = {
        "version": "version",
        "digest": "oci_digest",
        "source_commit": "commit",
        "source_sha256": "source_sha256",
        "transaction_token": "token",
    }
    if not isinstance(record, dict) or any(
        record.get(key) != identity[value] for key, value in fields.items()
    ):
        raise JournalBlocked("STRICT_RECEIPT_IDENTITY_MISMATCH")


def observe(journal, source_version: Path) -> dict:
    """Inspect the existing root-private receipt, never advance a stage."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise JournalBlocked("STRICT_RECEIPT_QA_REQUIRED")
    state = journal.inspect()
    if (
        state["decision"] != "RECONCILE_REQUIRED"
        or state["stage"] != "stage_strict_supervisor"
    ):
        raise JournalBlocked("STRICT_RECEIPT_INTENT_REQUIRED")
    supervisor = helper("supervised-container.py")
    rescue = helper("stage-guarded-update.py")
    path = STAGE / "deployed.json"
    supervisor.require_root_ancestors(path)
    record = rescue.read_private_receipt(path)
    check_identity(record, journal.identity)
    image = supervisor.verify_receipt(
        path, source_version, True, journal.identity["commit"], STAGE
    )
    if image != supervisor.IMAGE + journal.identity["oci_digest"]:
        raise JournalBlocked("STRICT_RECEIPT_IMAGE_MISMATCH")
    if record != rescue.read_private_receipt(path):
        raise JournalBlocked("STRICT_RECEIPT_CHANGED")
    return {
        "status": "PASS_QA_RECEIPT_IDENTITY_ONLY",
        "receipt_sha256": digest(record),
        "identity": dict(journal.identity),
        "live_image_verified": False,
        "supervisor_installed": False,
        "journal_advanced": False,
        "release_authorized": False,
        "production_modified": False,
    }
