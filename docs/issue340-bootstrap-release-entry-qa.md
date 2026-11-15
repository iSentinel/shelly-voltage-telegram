# Issue #340 — protected direct and piped installer entrypoints (QA ONLY)

**Draft on #398, not deployed. Production NO_GO.**

The legacy `bootstrap.sh` and `install-release.sh` used
`#!/usr/bin/env bash` and documentation examples piping downloaded
code to ordinary `bash`. Even after protecting the bundled
`install.sh`, a forced ordinary interpreter can run untrusted
`BASH_ENV` before any argument validation. Piping a script
directly to Bash also bypasses every shebang.

Both entrypoint shebangs now use `#!/bin/bash -p`. Examples in
those files use explicit `/bin/bash -p -s -- --non-interactive`.
QA executes only `--help` both through the protected direct file
entry and via the protected stdin pipe under poisoned `BASH_ENV`.
A deliberately unsafe plain Bash negative control reproduces the
injection risk. No archives, network, Docker, Telegram or SQLite
are accessed by the tests.

**Not a distribution trust proof:** fetching a mutable branch or
piping network code to root remains dangerous even with `-p`.
Any real installation still requires pinned authenticated source,
signed OCI identity, independent staging, verified writer freeze,
backups, rollback and Owner Approval. Manual/emergency caller
inventory, complete global G/B/W/A fence and physical blackout
acceptance remain open. Public PHOTO is separately NO_GO.
