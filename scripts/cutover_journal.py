"""Durable QA transaction bookkeeping; never authorize or execute a cutover."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

STAGES = (
    "verify_signed_pair",
    "inhibit_legacy_restarts",
    "stop_legacy_writer",
    "capture_newest_rescue",
    "stage_strict_supervisor",
    "verify_staged_runtime",
    "activate_new_writer",
    "accept_runtime",
)
SHA = re.compile(r"[a-f0-9]{64}")
COMMIT = re.compile(r"[a-f0-9]{40}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{5,95}")
MAX_BYTES = 65536


class JournalBlocked(ValueError):
    """An ambiguous or incomplete transaction must be reconciled."""


def canonical(value: dict) -> bytes:
    """Serialize deterministically for consistency checks."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value: dict) -> str:
    """Return a consistency checksum, not an authentication signature."""
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_identity(identity: dict) -> None:
    """Bind the journal to immutable release and transaction identifiers."""
    patterns = {
        "version": VERSION,
        "commit": COMMIT,
        "source_sha256": SHA,
        "oci_digest": re.compile(r"sha256:[a-f0-9]{64}"),
        "token": TOKEN,
    }
    if not isinstance(identity, dict) or set(identity) != set(patterns):
        raise JournalBlocked("IDENTITY_FIELDS")
    for key, pattern in patterns.items():
        value = identity[key]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise JournalBlocked("IDENTITY_INVALID")


def next_event(events: list) -> tuple[str, str]:
    """Compute only the next expected record; never execute an operation."""
    index = len(events) // 2
    if index >= len(STAGES):
        return "complete", ""
    return ("observe" if len(events) % 2 else "intent", STAGES[index])


def validate_event(event: dict, previous: list) -> None:
    """Reject unknown fields, reordered events and mismatched evidence."""
    kind, stage = next_event(previous)
    keys = {"kind", "stage", "previous_sha256"}
    if kind == "observe":
        keys.add("evidence_sha256")
    if kind == "complete" or not isinstance(event, dict):
        raise JournalBlocked("EVENT_ORDER")
    if set(event) != keys or (event["kind"], event["stage"]) != (kind, stage):
        raise JournalBlocked("EVENT_ORDER")
    expected = digest(previous[-1]) if previous else "0" * 64
    if event["previous_sha256"] != expected:
        raise JournalBlocked("EVENT_CHAIN")
    if kind == "observe":
        proof = event["evidence_sha256"]
        if not isinstance(proof, str) or not SHA.fullmatch(proof):
            raise JournalBlocked("EVIDENCE_DIGEST_INVALID")


def validate_state(state: dict, identity: dict) -> None:
    """Validate all records before allowing any update."""
    if not isinstance(state, dict) or set(state) != {
        "identity",
        "events",
        "checksum",
    }:
        raise JournalBlocked("JOURNAL_FIELDS")
    validate_identity(state["identity"])
    if state["identity"] != identity:
        raise JournalBlocked("IDENTITY_CHANGED")
    body = {"identity": state["identity"], "events": state["events"]}
    if state["checksum"] != digest(body):
        raise JournalBlocked("JOURNAL_CHECKSUM")
    events = body["events"]
    if not isinstance(events, list) or len(events) > len(STAGES) * 2:
        raise JournalBlocked("JOURNAL_LENGTH")
    prior = []
    for event in events:
        validate_event(event, prior)
        prior.append(event)


def require_private(info: os.stat_result, directory: bool = False) -> None:
    """Reject symlinks, linked files and nonprivate journal storage."""
    mode = 0o700 if directory else 0o600
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not kind(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != mode
        or (not directory and info.st_nlink != 1)
    ):
        raise JournalBlocked("JOURNAL_STORAGE_UNSAFE")


class Journal:
    """Persist intent and observations; evidence still requires real checks."""

    def __init__(self, directory: Path, identity: dict):
        """Require an existing private QA directory and exact identity."""
        validate_identity(identity)
        if not directory.is_absolute() or directory != directory.resolve():
            raise JournalBlocked("JOURNAL_PATH_UNSAFE")
        require_private(directory.lstat(), directory=True)
        self.directory = directory
        self.identity = dict(identity)
        self.path = directory / "state.json"

    @contextmanager
    def locked(self):
        """Serialize competing local journal writers."""
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        fd = os.open(self.directory / ".journal.lock", flags, 0o600)
        try:
            require_private(os.fstat(fd))
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise JournalBlocked("JOURNAL_BUSY") from exc
            yield
        finally:
            os.close(fd)

    def read(self) -> dict:
        """Load bounded regular-file JSON and fail closed on corruption."""
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require_private(info)
            if not 0 < info.st_size <= MAX_BYTES:
                raise JournalBlocked("JOURNAL_SIZE")
            try:
                state = json.loads(stream.read(MAX_BYTES + 1))
            except (ValueError, UnicodeError) as exc:
                raise JournalBlocked("JOURNAL_JSON") from exc
        validate_state(state, self.identity)
        return state

    def persist(self, events: list) -> None:
        """Commit by atomic replace and fsync file plus parent directory."""
        body = {"identity": self.identity, "events": events}
        data = canonical({**body, "checksum": digest(body)})
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".state-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            directory = os.open(
                self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.lexists(name):
                os.unlink(name)

    def initialize(self) -> None:
        """Create one journal; refuse an existing or dangling state path."""
        with self.locked():
            if os.path.lexists(self.path):
                raise JournalBlocked("JOURNAL_EXISTS")
            self.persist([])

    def append(self, kind: str, stage: str, evidence: str = "") -> None:
        """Persist exactly one next intent or externally observed result."""
        with self.locked():
            events = self.read()["events"]
            event = {
                "kind": kind,
                "stage": stage,
                "previous_sha256": digest(events[-1]) if events else "0" * 64,
            }
            if kind == "observe":
                event["evidence_sha256"] = evidence
            elif evidence:
                raise JournalBlocked("INTENT_EVIDENCE_UNEXPECTED")
            validate_event(event, events)
            self.persist([*events, event])

    def inspect(self) -> dict:
        """An unpaired intent requests reconciliation, not automatic retry."""
        with self.locked():
            events = self.read()["events"]
            kind, stage = next_event(events)
        decision = {
            "intent": "NEXT_REVIEW",
            "observe": "RECONCILE_REQUIRED",
            "complete": "RECORDS_COMPLETE_ONLY",
        }[kind]
        return {
            "decision": decision,
            "stage": stage,
            "records": len(events),
            "release_authorized": False,
            "production_modified": False,
        }
