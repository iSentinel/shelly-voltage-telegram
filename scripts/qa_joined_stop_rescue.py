"""Join existing QA observations without performing any cutover action."""

from __future__ import annotations

from argparse import Namespace

from cutover_journal import Journal, JournalBlocked, digest, next_event
from qa_observe_editor_drain import observe as observe_editor
from qa_observe_legacy_stop import observe as observe_legacy
from qa_observe_rescue import observe as observe_rescue


def pending_state(journal: Journal, stage: str) -> dict:
    """Capture a validated journal head for compare-and-append."""
    with journal.locked():
        state = journal.read()
    if next_event(state["events"]) != ("observe", stage):
        raise JournalBlocked("JOINED_INTENT_REQUIRED")
    return state


def prior_digest(state: dict, stage: str) -> str:
    """Look up the exact earlier observed stage in the validated chain."""
    for event in state["events"]:
        if event["kind"] == "observe" and event["stage"] == stage:
            return event["evidence_sha256"]
    raise JournalBlocked("JOINED_PRIOR_OBSERVATION_MISSING")


def record_stop(journal: Journal, args: Namespace) -> dict:
    """Bind a stopped container to the earlier inhibition observation."""
    stage = "stop_legacy_writer"
    state = pending_state(journal, stage)
    if args.require_stopped is not True:
        raise JournalBlocked("JOINED_STOP_REQUIRED")
    stopped = observe_legacy(args)
    if stopped.get("selected_container_stopped") is not True:
        raise JournalBlocked("JOINED_STOP_REQUIRED")
    prior = prior_digest(state, "inhibit_legacy_restarts")
    # Inhibition may have been observed before or after the stop.
    possible = {
        digest(
            dict(
                stopped,
                stage="inhibit_legacy_restarts",
                selected_container_stopped=value,
            )
        )
        for value in (True, False)
    }
    if prior not in possible:
        raise JournalBlocked("JOINED_LEGACY_IDENTITY_CHANGED")
    journal.append(
        "observe", stage, digest(stopped), expected_checksum=state["checksum"]
    )
    return stopped


def check_same_owner(
    stopped: dict, editor: dict, rescue: dict, token: str
) -> None:
    """Never join evidence from different containers or transactions."""
    ident = stopped["container_id"]
    if (
        stopped.get("selected_container_stopped") is not True
        or editor.get("container_id") != ident
        or rescue.get("container_id") != ident
        or rescue.get("container_image") != stopped["container_image"]
        or rescue.get("token") != token
        or rescue.get("pending_token_verified") is not True
        or rescue.get("logical_history_parity") is not True
    ):
        raise JournalBlocked("JOINED_OWNER_OR_RESCUE_CHANGED")


def record_rescue(
    journal: Journal, args: Namespace, editor: Namespace
) -> dict:
    """Observe the stopped owner, retired editor and same-token rescue."""
    stage = "capture_newest_rescue"
    state = pending_state(journal, stage)
    token = journal.identity["token"]
    if args.require_stopped is not True or args.token != token:
        raise JournalBlocked("JOINED_RESCUE_TOKEN_OR_STOP_REQUIRED")
    stopped = observe_legacy(args)
    if digest(stopped) != prior_digest(state, "stop_legacy_writer"):
        raise JournalBlocked("JOINED_STOP_OBSERVATION_CHANGED")
    if editor.container != args.container:
        raise JournalBlocked("JOINED_EDITOR_CONTAINER_CHANGED")
    drained = observe_editor(editor)
    rescue = observe_rescue(args)
    check_same_owner(stopped, drained, rescue, token)
    if stopped != observe_legacy(args) or drained != observe_editor(editor):
        raise JournalBlocked("JOINED_LIVE_STATE_CHANGED")
    report = {
        "status": "PASS_QA_JOINED_STOP_RESCUE_ONLY",
        "identity": dict(journal.identity),
        "stopped": stopped,
        "editor": drained,
        "rescue": rescue,
        "all_writers_verified": False,
        "remote_delivery_reconciled": False,
        "release_authorized": False,
        "production_modified": False,
    }
    journal.append(
        "observe", stage, digest(report), expected_checksum=state["checksum"]
    )
    return report
