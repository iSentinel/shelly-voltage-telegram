#!/usr/bin/env python3
"""Activate signed source and OCI together while retaining current SQLite."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

import github_update as updater
from loguru import logger

MONITOR = "shelly-voltage-compose.service"
SUPERVISOR = "shelly-voltage-supervised.service"
PHOTO_TIMER = "shelly-incident-photo-refresh.timer"
PHOTO_SERVICE = "shelly-incident-photo-refresh.service"
CONTAINER = "shelly-voltage-bot"
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram@"
READY = "http://127.0.0.1:8080/ready"
LEGACY_START = "/bin/bash /opt/shelly-voltage/scripts/start-compose.sh"
LEGACY_RECOVER = (
    "/bin/bash /opt/shelly-voltage/scripts/apply-update.sh --recover"
)
GUARDED_START = (
    "/bin/bash /opt/shelly-voltage/scripts/apply-update.sh --guarded-start"
)
START_DIRECTIVES = ("ExecStart=", "ExecReload=")
START_HOOKS = ("ExecCondition=", "ExecStartPre=", "ExecStartPost=")
ID_PATTERN = re.compile(r"cutover-[A-Za-z0-9_]{8}\Z")
PHOTO_PATTERN = re.compile(r"[1-9][0-9]{0,18}\Z")
DIGEST_PATTERN = re.compile(r"sha256:[a-f0-9]{64}\Z")
PENDING = (
    "telegram_graphic_publish_pending",
    "telegram_legacy_text_confirm_pending",
    "telegram_recovery_rotation_pending_at",
)


@dataclass(frozen=True)
class Layout:
    """Use fixed production paths at the CLI boundary."""

    source: Path = Path("/opt/shelly-voltage")
    state: Path = Path("/var/lib/shelly-voltage-updater")
    database: Path = Path("/var/lib/shelly-voltage/voltage.sqlite3")
    unit: Path = Path(f"/etc/systemd/system/{MONITOR}")
    operator_lock: Path = Path("/run/lock/shelly-signed-forward.lock")
    updater_lock: Path = Path("/run/lock/shelly-voltage-updater.lock")
    overlay: Path = Path("/etc/shelly-voltage/yasno-reader.yaml")
    public_state: Path = Path("/var/lib/shelly-voltage-update-check")

    @property
    def home(self) -> Path:
        """Keep both source trees on the installed source filesystem."""
        return self.source.parent / ".shelly-signed-forward"

    @property
    def marker(self) -> Path:
        """Use the marker recognized by both releases' startup entry points."""
        return self.state / "pending-update"


def run(*args: str, timeout: int = 20) -> str:
    """Execute a fixed operator command without exposing its private output."""
    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/root",
        "LC_ALL": "C.UTF-8",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
    }
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if result.returncode:
        raise ValueError(f"command failed: {args[0]} ({result.returncode})")
    return result.stdout.strip()


def safe_path(path: Path, *, folder: bool = False) -> os.stat_result:
    """Reject links, writable ancestors and files outside root control."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("protected path must be absolute")
    for parent in path.parents:
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
        ):
            raise ValueError("protected path has an unsafe parent")
    info = path.lstat()
    valid = (
        stat.S_ISDIR(info.st_mode)
        if folder
        else (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    )
    if not valid or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("protected path is unsafe")
    return info


def durable(path: Path) -> None:
    """Flush a changed directory after an atomic replacement."""
    fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Replace one protected file atomically and flush its directory."""
    fd, name = tempfile.mkstemp(prefix=".cutover-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        durable(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def record(path: Path, values: dict) -> None:
    """Persist a private cutover checkpoint."""
    write_file(path, json.dumps(values, sort_keys=True).encode() + b"\n")


@contextmanager
def lease(path: Path):
    """Serialize operators and share the installed updater's lock inode."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "rb+") as lock:
        info = os.fstat(lock.fileno())
        linked = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            raise ValueError("invalid updater lock")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def source_version(root: Path) -> str:
    """Require the source and release markers to identify the same release."""
    one, two = root / "VERSION", root / "RELEASE_VERSION"
    for path in (one, two):
        safe_path(path)
    version = one.read_text().strip()
    updater.version_tuple(version)
    if two.read_text().strip() != version:
        raise ValueError("installed version markers disagree")
    return version


def image_pin(digest: str) -> str:
    """Restrict Docker selection to the published repository and digest."""
    if not DIGEST_PATTERN.fullmatch(digest):
        raise ValueError("invalid immutable image digest")
    return IMAGE + digest


def signed_candidate(args: argparse.Namespace) -> dict:
    """Verify exact source bytes and both signatures before extracting."""
    helper = Path(__file__).with_name("verify-signed-release-pair.py")
    for item in (args.archive, args.bundle, args.sha256sums):
        safe_path(item)
    with args.archive.open("rb") as stream:
        approved = hashlib.file_digest(stream, "sha256").hexdigest()
    if approved != args.archive_sha256:
        raise ValueError("archive hash differs from the approved release")
    output = run(
        sys.executable,
        "-B",
        str(helper),
        "--version",
        args.version,
        "--commit",
        args.commit,
        "--digest",
        args.digest,
        "--archive",
        str(args.archive),
        "--bundle",
        str(args.bundle),
        "--sha256sums",
        str(args.sha256sums),
        "--jsonl",
        timeout=220,
    )
    proof = json.loads(output)
    verify_pair_proof(proof, args)
    return {
        "version": args.version,
        "commit": args.commit,
        "digest": args.digest,
        "archive_sha256": approved,
    }


def verify_pair_proof(proof: dict, args: argparse.Namespace) -> None:
    """Require verification of both immutable subjects and attestations."""
    if (
        proof["status"] != "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY"
        or proof["version"] != args.version
        or proof["commit"] != args.commit
        or proof["digest_suffix"] != args.digest[-12:]
        or not all(
            proof[field] is True
            for field in (
                "source_signature_verified",
                "oci_signature_verified",
                "oci_attestations_verified",
            )
        )
    ):
        raise ValueError("signed release pair proof is incomplete")


def stage_source(archive: Path, work: Path, pair: dict) -> None:
    """Extract just the signed source tree into the private workspace."""
    version = pair["version"]
    prefix = f"shelly-voltage-telegram-{version}"
    with archive.open("rb") as stream:
        if (
            hashlib.file_digest(stream, "sha256").hexdigest()
            != pair["archive_sha256"]
        ):
            raise ValueError("archive changed after signature verification")
        stream.seek(0)
        with tarfile.open(fileobj=stream, mode="r:gz") as tar:
            extract_source(tar, work, prefix)
    extracted = work / prefix
    os.rename(extracted, work / "next")
    if source_version(work / "next") != version:
        raise ValueError("staged source version mismatch")


def extract_source(tar: tarfile.TarFile, work: Path, prefix: str) -> None:
    """Reject links and paths outside the single expected archive root."""
    for member in tar.getmembers():
        parts = Path(member.name).parts
        if (
            not parts
            or parts[0] != prefix
            or ".." in parts
            or not (member.isfile() or member.isdir())
        ):
            raise ValueError("unexpected archive entry")
    tar.extractall(work, filter="data")


def stage_image(pair: dict) -> None:
    """Download and read back the source revision of the signed image."""
    pin = image_pin(pair["digest"])
    run("docker", "pull", pin, timeout=150)
    output = run(
        "docker",
        "image",
        "inspect",
        "--format",
        '{{index .Config.Labels "org.opencontainers.image.revision"}}'
        '|{{index .Config.Labels "org.opencontainers.image.version"}}',
        pin,
    )
    if output != f"{pair['commit']}|v{pair['version']}":
        raise ValueError("staged OCI revision mismatch")


def state_of(unit: str) -> str:
    """Read one effective systemd state."""
    return run("systemctl", "show", "-p", "ActiveState", "--value", unit)


def wait_stopped(unit: str) -> None:
    """Allow an existing edit to finish without interrupting its request."""
    for _ in range(20):
        if state_of(unit) in ("inactive", "failed"):
            return
        time.sleep(1)
    raise ValueError("temporary photo editor did not stop")


def docker_state() -> tuple[str, str, str]:
    """Read the immutable image, runtime state and restart mode."""
    result = run(
        "docker",
        "inspect",
        "--format",
        "{{.Config.Image}}|{{.State.Status}}|"
        "{{.HostConfig.RestartPolicy.Name}}",
        CONTAINER,
    ).split("|")
    if len(result) != 3:
        raise ValueError("Docker container state was invalid")
    return result[0], result[1], result[2]


def verify_database_mount(mounts, paths: Layout) -> None:
    """Require the current SQLite directory and forbid replacement code."""
    data = []
    for target, source, kind, writable in mounts:
        if target == "/data":
            data.append((source, kind, writable))
            continue
        allowed = target in ("/run/yasno", "/var/backups/shelly-voltage")
        if (
            kind == "bind"
            and writable is False
            and (allowed or re.fullmatch(r"/run/secrets/[a-z0-9_]+", target))
        ):
            continue
        raise ValueError(
            "unrecognized Docker mount could override release files"
        )
    if data != [(str(paths.database.parent), "bind", True)]:
        raise ValueError(
            "Docker does not use the expected live SQLite directory"
        )


def verify_current_mounts(paths: Layout) -> None:
    """Inspect only the mounted paths and one database environment entry."""
    mounts = json.loads(
        run("docker", "inspect", "--format", "{{json .Mounts}}", CONTAINER)
    )
    verify_database_mount(
        ((m["Destination"], m["Source"], m["Type"], m["RW"]) for m in mounts),
        paths,
    )
    database = run(
        "docker",
        "inspect",
        "--format",
        "{{range .Config.Env}}"
        '{{if eq (index (split . "=") 0) "DATABASE_PATH"}}'
        "{{println .}}{{end}}{{end}}",
        CONTAINER,
    )
    if database != f"DATABASE_PATH=/data/{paths.database.name}":
        raise ValueError("Docker points to a different SQLite filename")


def boot_layout(paths: Layout) -> None:
    """Require one known Compose owner and an unmodified unit path."""
    fragment = run(
        "systemctl", "show", "-p", "FragmentPath", "--value", MONITOR
    )
    dropins = run("systemctl", "show", "-p", "DropInPaths", "--value", MONITOR)
    if (
        fragment != str(paths.unit)
        or dropins
        or state_of(SUPERVISOR) not in ("inactive", "failed")
    ):
        raise ValueError("installed service has a different startup owner")
    safe_path(paths.unit)
    if run("systemctl", "is-enabled", MONITOR) != "enabled":
        raise ValueError("the existing Compose service is not enabled")


def read_pair(paths: Layout) -> dict:
    """Check that installed source and on-disk Docker pin agree."""
    for name in ("deployed.json", "override.yaml"):
        safe_path(paths.state / name)
    value = json.loads((paths.state / "deployed.json").read_text())
    pin = image_pin(value["digest"])
    lines = (paths.state / "override.yaml").read_text().splitlines()
    if [x.strip() for x in lines if x.strip()] != [
        "services:",
        "shelly-voltage:",
        f"image: {pin}",
    ] or source_version(paths.source) != value["version"]:
        raise ValueError("installed source and immutable image pin differ")
    return value


def photo_identity(db: sqlite3.Connection, expected: str) -> str:
    """Require the existing photo and no ambiguous Telegram operations."""
    meta = dict(
        db.execute(
            "SELECT key, value FROM meta WHERE key IN (?,?,?,?,?)",
            (
                "telegram_status_message_id",
                "telegram_status_media_identity",
                *PENDING,
            ),
        )
    )
    marker = meta.get("telegram_status_media_identity", "")
    if (
        meta.get("telegram_status_message_id") != expected
        or not re.fullmatch(rf"photo:-?[0-9]+:{re.escape(expected)}", marker)
        or any(key in meta for key in PENDING)
    ):
        raise ValueError(
            "Telegram photo identity or pending publication changed"
        )
    return marker


def read_photo(paths: Layout, expected: str) -> str:
    """Read the existing status through a SQLite snapshot."""
    with closing(
        sqlite3.connect(
            paths.database.as_uri() + "?mode=ro", uri=True, timeout=5
        )
    ) as db:
        return photo_identity(db, expected)


def history_digest(db: sqlite3.Connection) -> str:
    """Hash complete logical history and the persistent SQLite version."""
    result = hashlib.sha256()
    for pragma in ("user_version", "application_id"):
        result.update(str(db.execute(f"PRAGMA {pragma}").fetchone()).encode())
    for line in db.iterdump():
        result.update(line.encode())
        result.update(b"\n")
    return result.hexdigest()


def backup_database(paths: Layout, work: Path, photo: str) -> None:
    """Save committed SQLite pages and compare the complete history."""
    destination = work / "database-before.sqlite3"
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with (
        closing(
            sqlite3.connect(
                paths.database.as_uri() + "?mode=ro", uri=True, timeout=5
            )
        ) as source,
        closing(sqlite3.connect(destination, timeout=5)) as backup,
    ):
        source.execute("BEGIN")
        source.backup(backup)
        if backup.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("rescue SQLite integrity check failed")
        if photo_identity(source, photo) != photo_identity(backup, photo):
            raise ValueError("rescue SQLite photo identity changed")
        if history_digest(source) != history_digest(backup):
            raise ValueError("rescue SQLite history changed")
    with destination.open("rb") as stream:
        os.fsync(stream.fileno())
    durable(work)


def installed_runtime(paths: Layout) -> tuple[dict, str]:
    """Check the sole startup owner and its exact running release."""
    if paths.marker.exists() or paths.marker.is_symlink():
        raise ValueError("a different update is already pending")
    boot_layout(paths)
    if state_of(MONITOR) != "active":
        raise ValueError("the Compose startup owner is not active")
    safe_path(paths.state, folder=True)
    database = paths.database.lstat()
    if not stat.S_ISREG(database.st_mode) or database.st_nlink != 1:
        raise ValueError("the live database is not an ordinary file")
    installed = read_pair(paths)
    image, status, policy = docker_state()
    if image != image_pin(installed["digest"]) or status != "running":
        raise ValueError("live Docker image does not match the installed pair")
    if policy not in ("no", "unless-stopped"):
        raise ValueError("unrecognized Docker restart policy")
    return installed, policy


def capture(paths: Layout, work: Path, pair: dict, photo: str) -> dict:
    """Record the existing pair before touching any running writer."""
    installed, policy = installed_runtime(paths)
    if updater.version_tuple(pair["version"]) <= updater.version_tuple(
        installed["version"]
    ):
        raise ValueError("the signed release must advance the current version")
    read_photo(paths, photo)
    updater.preflight(paths.database, READY)
    for name in ("deployed.json", "override.yaml"):
        write_file(
            work / f"previous-{name}", (paths.state / name).read_bytes()
        )
    verify_current_mounts(paths)
    previous_unit = paths.unit.read_bytes()
    bridge_unit(previous_unit, work / "boot-guard.sh")
    write_file(work / "previous.service", previous_unit)
    state = {
        "phase": "staged",
        "pair": pair,
        "previous": installed,
        "restart": policy,
        "photo": photo,
        "timer_active": state_of(PHOTO_TIMER) == "active",
        "timer_enabled": run(
            "systemctl", "show", "-p", "UnitFileState", "--value", PHOTO_TIMER
        )
        == "enabled",
    }
    record(work / "transaction.json", state)
    return state


def runtime_config(paths: Layout, work: Path, pair: dict) -> None:
    """Preserve private configuration and validate the effective image pin."""
    target = work / "next"
    guarded_unit(target)
    require_legacy_compatible(target)
    copy_configuration(paths.source, target)
    pin = image_pin(pair["digest"])
    write_file(
        work / "next-override.yaml",
        f"services:\n  shelly-voltage:\n    image: {pin}\n".encode(),
    )
    command = [
        "docker",
        "compose",
        "-f",
        str(target / "compose.yaml"),
        "-f",
        str(work / "next-override.yaml"),
    ]
    if paths.overlay.exists() or paths.overlay.is_symlink():
        safe_path(paths.overlay)
        command.extend(("-f", str(paths.overlay)))
    services = json.loads(run(*command, "config", "--format", "json"))[
        "services"
    ]
    validate_compose(paths, services["shelly-voltage"], pin)


def require_legacy_compatible(source: Path) -> None:
    """Reject a signed application that cannot start under legacy Compose."""
    app = source / "app.py"
    safe_path(app)
    # Inspect the signed entry point without starting another SQLite writer.
    if "SVT_FENCE_DIR_REQUIRED" in app.read_text():
        raise ValueError("candidate requires the sole-writer supervisor")


def validate_compose(paths: Layout, monitor: dict, pin: str) -> None:
    """Verify that the candidate uses the intended pin and existing data."""
    if monitor["image"] != pin or monitor["restart"] != "no":
        raise ValueError(
            "effective Compose plan violates the signed pin or restart policy"
        )
    verify_database_mount(
        (
            (
                m["target"],
                m.get("source"),
                m["type"],
                not m.get("read_only", False),
            )
            for m in monitor["volumes"]
        ),
        paths,
    )
    if (
        monitor["environment"].get("DATABASE_PATH")
        != f"/data/{paths.database.name}"
    ):
        raise ValueError("candidate Compose points to a different SQLite file")


def copy_configuration(source: Path, target: Path) -> None:
    """Keep local configuration, secret ownership and source access modes."""
    for name in (".env", "secrets"):
        original = source / name
        safe_path(original, folder=name == "secrets")
        if original.is_dir() and any(
            p.is_symlink() for p in original.rglob("*")
        ):
            raise ValueError("existing secret directory contains a link")
        run("cp", "-a", "--", str(original), str(target / name))
    for relative in (Path("."), Path("scripts")):
        info = (source / relative).stat()
        os.chown(target / relative, info.st_uid, info.st_gid)
        os.chmod(target / relative, stat.S_IMODE(info.st_mode))


def guarded_unit(source: Path) -> bytes:
    """Require the released systemd unit that checks pending startup state."""
    path = source / "systemd" / MONITOR
    safe_path(path)
    payload = path.read_bytes()
    if startup_commands(payload) != startup_pair(
        GUARDED_START
    ) or unit_directives(payload, START_HOOKS):
        raise ValueError(
            "candidate systemd unit lacks the expected startup guard"
        )
    return payload


def startup_commands(payload: bytes) -> list[str]:
    """Identify every start and reload command, including overrides."""
    return unit_directives(payload, START_DIRECTIVES)


def unit_assignment(line: str) -> str:
    """Normalize whitespace around a systemd directive's equals sign."""
    key, separator, value = line.strip().partition("=")
    return key.rstrip() + separator + value.lstrip()


def unit_directives(payload: bytes, names: tuple[str, ...]) -> list[str]:
    """Read all matching unit assignments, including repeated definitions."""
    lines = map(unit_assignment, payload.decode().splitlines())
    return [line for line in lines if line.startswith(names)]


def startup_pair(command: str) -> list[str]:
    """Use the same guarded command for start and reload."""
    return [f"{directive}{command}" for directive in START_DIRECTIVES]


def bridge_unit(previous: bytes, helper: Path) -> bytes:
    """Keep the existing unit settings and guard start and reload."""
    commands = startup_commands(previous)
    if commands not in (
        startup_pair(LEGACY_START),
        startup_pair(GUARDED_START),
    ):
        raise ValueError("the current systemd startup recipe is unrecognized")
    hooks = unit_directives(previous, START_HOOKS)
    legacy_recovery = f"ExecStartPre={LEGACY_RECOVER}"
    if hooks not in ([], [legacy_recovery]):
        raise ValueError("the current systemd startup hook is unrecognized")
    replacement = dict(
        zip(commands, startup_pair(f"/bin/bash {helper} --guarded-start"))
    )
    return (
        "\n".join(
            replacement.get(unit_assignment(line), line)
            for line in previous.decode().splitlines()
            if unit_assignment(line) != legacy_recovery
        )
        + "\n"
    ).encode()


def arm_boot_guard(paths: Layout, work: Path) -> None:
    """Install the signed startup guard before publishing any pending state."""
    release_script = work / "next" / "scripts" / "apply-update.sh"
    safe_path(release_script)
    helper = work / "boot-guard.sh"
    write_file(helper, release_script.read_bytes())
    previous = (work / "previous.service").read_bytes()
    write_file(paths.unit, bridge_unit(previous, helper), 0o644)
    run("systemctl", "daemon-reload")


def marker_bytes(work: Path) -> bytes:
    """Use a token the legacy rollback path will refuse to interpret."""
    return f"signed-forward:{work.name}\n".encode()


def make_marker(paths: Layout, work: Path) -> None:
    """Claim the pending marker only when it is absent."""
    if paths.marker.exists() or paths.marker.is_symlink():
        raise ValueError("a different update is already pending")
    write_file(paths.marker, marker_bytes(work))


def clear_marker(paths: Layout, work: Path) -> None:
    """Clear only the marker belonging to this coherent transaction."""
    safe_path(paths.marker)
    if paths.marker.read_bytes() != marker_bytes(work):
        raise ValueError("pending marker belongs to another transaction")
    paths.marker.unlink()
    durable(paths.state)


def freeze(paths: Layout, work: Path, saved: dict) -> None:
    """Stop the photo editor and block autonomous startup before the swap."""
    if saved["timer_active"]:
        run("systemctl", "stop", PHOTO_TIMER)
    if saved["timer_enabled"]:
        run("systemctl", "disable", PHOTO_TIMER)
    wait_stopped(PHOTO_SERVICE)
    run("docker", "update", "--restart=no", CONTAINER)
    make_marker(paths, work)
    run("systemctl", "stop", MONITOR, timeout=45)
    image, status, policy = docker_state()
    if (image, status, policy) != (
        image_pin(saved["previous"]["digest"]),
        "exited",
        "no",
    ):
        raise ValueError("the existing writer did not stop")
    backup_database(paths, work, saved["photo"])


def install_pair(paths: Layout, work: Path, saved: dict) -> None:
    """Install both components while the pending guard blocks startup."""
    os.rename(paths.source, work / "previous")
    durable(paths.source.parent)
    durable(work)
    os.rename(work / "next", paths.source)
    durable(paths.source.parent)
    durable(work)
    pair = saved["pair"]
    write_file(
        paths.state / "override.yaml",
        (work / "next-override.yaml").read_bytes(),
    )
    record(paths.state / "deployed.json", pair)
    write_file(paths.unit, guarded_unit(paths.source), mode=0o644)
    run("sync", "-f", str(paths.source))
    run("systemctl", "daemon-reload")
    if read_pair(paths) != pair:
        raise ValueError("installed signed pair cannot be read back")


def save_phase(work: Path, saved: dict, phase: str) -> None:
    """Record a recovery decision before making it externally visible."""
    saved["phase"] = phase
    record(work / "transaction.json", saved)


def exact_marker_exists(paths: Layout, work: Path) -> bool:
    """Stop on foreign markers instead of moving their update state."""
    if not (paths.marker.exists() or paths.marker.is_symlink()):
        return False
    safe_path(paths.marker)
    if paths.marker.read_bytes() != marker_bytes(work):
        raise ValueError("pending marker belongs to another transaction")
    return True


def restore_preboot(paths: Layout, work: Path, saved: dict) -> None:
    """Restore the known old pair before the candidate ever starts."""
    if saved["phase"] not in ("preboot", "previous-pair-restored"):
        raise ValueError("the previous pair is outside the recovery window")
    marked = exact_marker_exists(paths, work)
    original = work / "previous"
    if not marked and read_pair(paths) != saved["previous"]:
        raise ValueError("the installed pair changed outside this cutover")
    if original.exists():
        if not marked:
            raise ValueError("a partially switched pair has no owned marker")
        if paths.source.exists():
            if source_version(paths.source) != saved["pair"]["version"]:
                raise ValueError("the source changed outside this cutover")
            if (work / "failed-source").exists():
                raise ValueError("the failed source copy already exists")
            os.rename(paths.source, work / "failed-source")
            durable(paths.source.parent)
        os.rename(original, paths.source)
        durable(paths.source.parent)
        durable(work)
    elif source_version(paths.source) != saved["previous"]["version"]:
        raise ValueError("the previous source is missing or changed")
    for name in ("override.yaml", "deployed.json"):
        write_file(
            paths.state / name, (work / f"previous-{name}").read_bytes()
        )
    if read_pair(paths) != saved["previous"]:
        raise ValueError("the previous source and pin could not be verified")
    if marked:
        clear_marker(paths, work)
    write_file(paths.unit, (work / "previous.service").read_bytes(), 0o644)
    run("systemctl", "daemon-reload")
    save_phase(work, saved, "previous-pair-restored")


def verify_ready(paths: Layout, saved: dict) -> None:
    """Require three healthy reads and the same bound photo."""
    pin = image_pin(saved["pair"]["digest"])
    good = 0
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        try:
            updater.preflight(paths.database, READY)
            if docker_state() != (pin, "running", "no"):
                raise ValueError("unexpected live runtime")
            read_photo(paths, saved["photo"])
        except (OSError, ValueError, sqlite3.Error):
            good = 0
        else:
            good += 1
            if good == 3:
                return
        time.sleep(2)
    raise ValueError("candidate readiness or photo continuity unconfirmed")


def activate(paths: Layout, work: Path, pair: dict, photo: str) -> None:
    """Use fail-closed recovery across the source and image switch."""
    try:
        with lease(paths.updater_lock):
            saved = capture(paths, work, pair, photo)
            runtime_config(paths, work, pair)
            seal_pair(paths, work, saved)
    except (OSError, ValueError, subprocess.SubprocessError, sqlite3.Error):
        info = work / "transaction.json"
        if (
            info.exists()
            and json.loads(info.read_text())["phase"]
            == "previous-pair-restored"
        ):
            start_previous(work, json.loads(info.read_text()))
        raise
    start_candidate(paths, work, saved)


def seal_pair(paths: Layout, work: Path, saved: dict) -> None:
    """Make the switch recoverable before unlocking the systemd start path."""
    save_phase(work, saved, "preboot")
    try:
        arm_boot_guard(paths, work)
        freeze(paths, work, saved)
        install_pair(paths, work, saved)
    except (OSError, ValueError, subprocess.SubprocessError, sqlite3.Error):
        restore_preboot(paths, work, saved)
        raise
    save_phase(work, saved, "candidate-may-have-run")
    clear_marker(paths, work)


def start_candidate(paths: Layout, work: Path, saved: dict) -> None:
    """Start the coherent pair and leave it intact on verification failure."""
    run("systemctl", "start", MONITOR, timeout=65)
    verify_ready(paths, saved)
    verify_current_mounts(paths)
    if read_pair(paths) != saved["pair"]:
        raise ValueError("candidate source or pin changed after startup")
    publish_version(paths, saved["pair"])
    save_phase(work, saved, "verified")


def publish_version(paths: Layout, pair: dict) -> None:
    """Publish the read-only receipt only once the new pair is healthy."""
    paths.public_state.mkdir(mode=0o755, exist_ok=True)
    safe_path(paths.public_state, folder=True)
    value = {key: pair[key] for key in ("version", "digest")}
    write_file(
        paths.public_state / "deployed.json",
        json.dumps(value, sort_keys=True).encode() + b"\n",
        mode=0o644,
    )


def recover(paths: Layout, work: Path) -> None:
    """Restore a preboot failure, or resume only the already installed pair."""
    state_file = work / "transaction.json"
    safe_path(state_file)
    saved = json.loads(state_file.read_text())
    with lease(paths.updater_lock):
        if saved["phase"] == "candidate-may-have-run":
            if read_pair(paths) != saved["pair"]:
                raise ValueError(
                    "candidate may have run and installed pair drifted"
                )
            boot_layout(paths)
            if paths.unit.read_bytes() != guarded_unit(paths.source):
                raise ValueError("installed systemd unit changed")
            if exact_marker_exists(paths, work):
                clear_marker(paths, work)
        elif saved["phase"] in ("preboot", "previous-pair-restored"):
            restore_preboot(paths, work, saved)
        else:
            raise ValueError("no interrupted cutover to recover")
    if saved["phase"] == "candidate-may-have-run":
        start_candidate(paths, work, saved)
        return
    start_previous(work, saved)


def start_previous(work: Path, saved: dict) -> None:
    """Bring back only the pre-cutover runtime and its original timer mode."""
    run("systemctl", "start", MONITOR, timeout=65)
    if docker_state()[:2] != (
        image_pin(saved["previous"]["digest"]),
        "running",
    ):
        raise ValueError("previous runtime did not start")
    run("docker", "update", f"--restart={saved['restart']}", CONTAINER)
    if saved["timer_enabled"]:
        run("systemctl", "enable", PHOTO_TIMER)
    if saved["timer_active"]:
        run("systemctl", "start", PHOTO_TIMER)
    save_phase(work, saved, "recovered")


def options() -> argparse.Namespace:
    """Expose preparation, explicit activation and interrupted recovery."""
    cli = argparse.ArgumentParser(description=__doc__)
    modes = cli.add_subparsers(dest="action", required=True)
    for mode in ("prepare", "activate"):
        command = modes.add_parser(mode)
        for name in ("version", "commit", "digest", "archive-sha256", "photo"):
            command.add_argument(f"--{name}", required=True)
        for name in ("archive", "bundle", "sha256sums"):
            command.add_argument(f"--{name}", type=Path, required=True)
    modes.add_parser("recover").add_argument("--transaction", required=True)
    return cli.parse_args()


def dispatch(paths: Layout, args: argparse.Namespace) -> Path:
    """Choose a fixed production operation under the operator lease."""
    if args.action == "recover":
        if not ID_PATTERN.fullmatch(args.transaction):
            raise ValueError("invalid transaction name")
        work = paths.home / args.transaction
        safe_path(work, folder=True)
        recover(paths, work)
        return work
    if not PHOTO_PATTERN.fullmatch(args.photo):
        raise ValueError("the confirmed photo ID is invalid")
    pair = signed_candidate(args)
    work = Path(tempfile.mkdtemp(prefix="cutover-", dir=paths.home))
    logger.info("Private transaction {}", work.name)
    stage_source(args.archive, work, pair)
    stage_image(pair)
    if args.action == "activate":
        activate(paths, work, pair, args.photo)
    else:
        capture(paths, work, pair, args.photo)
        runtime_config(paths, work, pair)
    return work


def main() -> int:
    """Run one explicit root operation and retain its local evidence."""
    args = options()
    paths = Layout()
    try:
        if os.geteuid() != 0:
            raise ValueError("root is required")
        paths.home.mkdir(mode=0o700, exist_ok=True)
        if stat.S_IMODE(safe_path(paths.home, folder=True).st_mode) != 0o700:
            raise ValueError("cutover home must have private permissions")
        with lease(paths.operator_lock):
            work = dispatch(paths, args)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        sqlite3.Error,
        tarfile.TarError,
        subprocess.SubprocessError,
    ) as exc:
        logger.error(
            "Signed cutover blocked: {}",
            exc if isinstance(exc, ValueError) else type(exc).__name__,
        )
        return 3
    print(
        json.dumps(
            {"status": "PASS", "action": args.action, "transaction": work.name}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
