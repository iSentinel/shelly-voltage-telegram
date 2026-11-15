# Issue #340 — installer Python guard import isolation (QA ONLY)

**Stacked Draft on #396. No production deployment, NO_GO.**

The legacy installer still launched signed-image, source archive,
SQLite archive and deployment parity checks through ordinary
`python3`. Because the caller is privileged, inherited
`PYTHONPATH` could load a forged standard library module before
any guard code executed.

Five calls now use `/usr/bin/python3 -I`: both branches of
`installer-signed-image-guard.py`, source archive validation,
SQLite archive validation and signed deployment-state audit.
No validation rules, source/SQLite data, Docker or Telegram behavior
are changed by this patch.

Isolated QA invokes only each CLI's `--help` under a forged
`argparse.py`, static caller scans and a deliberately unprotected
negative control. All tests use disposable files and no Docker,
SQLite backup, network releases or systemd operations.

Residual obligations are unchanged: installer configure.sh and
manual/piped Bash, residual emergency callers, G/B/W/A old-owner
freeze, approved interrupted boot, final current signed OCI/source,
two WAL-complete independently restored backups across appropriate
failure domains, real Telegram long-poll teardown, physical outage
QA, independent security review and explicit Owner Approval of
possible full single-NVMe loss. Public pinned PHOTO remains NO_GO.
