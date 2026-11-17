# Issue #402: signed installer health and YASNO access regression

**Scope:** post-release QA work only. This is not a modification of the
already-signed `v0.5.22-hw` image or its deployed source files.

## Production evidence (17 November 2026)

The owner-approved `0.5.22-hw` release was deployed on LXC110. The
signed Docker image, application source, updater marker and Compose
override ultimately matched, and the 15-minute real voltage acceptance
was PASS. Two startup/install defects were observed:

1. The signed installer waited for three successful `/ready` probes,
   but then called `deployment-state-audit.py` **only once**. Docker's
   health state could still be `starting` even after Shelly returned
   fresh data. The installer exited with `RUNTIME_NOT_HEALTHY`.
   Subsequent independent audit returned PASS without code changes.
2. The filtered source tar extraction restored restrictive root-only
   `0700` directory modes. The separate `shelly-yasno` service account
   could no longer traverse the source root and `scripts` path.
   Its scheduled refresh failed despite having a valid prior cache.
   A local operational permission fix restored YASNO fetch/handoff.

## Next-release fix

`await_signed_ready()` retains its strict three-success Shelly check
and now retries the **full existing signed deployment parity audit**
within the same bounded 60-probe window. It returns success only after
the audit also passes. Failure to become healthy still exits non-zero,
and the last safe audit reasons are printed for diagnostics. It does
not bypass an invalid signature, wrong version, missing metadata or
running wrong image.

`prepare_yasno_reader()` runs after the source copy and private state
setup, before service activation. When the optional system account and
group both exist, it gives exactly:

- `/opt/shelly-voltage`: root:`shelly-yasno`, `0750`
- `/opt/shelly-voltage/scripts`: root:`shelly-yasno`, `0710`
- `.env`: remains `0600` root-owned
- `secrets`: remains `0700` root-owned

Other source files, mounted live SQLite and Telegram secrets are not
made writable by the forecast service. Missing optional YASNO account
is a no-op; dry-run is non-mutating. A mode violation on private state
blocks installation.

## Combined-candidate security review follow-up

The combined QA branch includes an additional fail-closed guard that
checks **file ownership** and symlinks before any directory group
permissions are modified. A file with mode 0600 but owned by the
forecast account is still readable by that account, so checking only
the mode is insufficient. The installer checks that its project root,
scripts directory, private .env and secrets directory are real paths
owned by the invoking account (root in a production installation).
It verifies .env 0600 and secrets 0700 before chgrp/chmod.

Two new synthetic scenarios reject unexpected config ownership and
a symlinked scripts directory, and assert that no permissions have
changed on failure. The existing insecure .env-mode test also verifies
that the directory modes and group operations remain untouched.
This extra hardening is present in the combined integration QA branch,
not in the original immutable v0.5.22-hw production image.

## Test coverage

`tests/test_install_ready_yasno_permissions_qa.py` runs only the
specific installer functions with fake network, Docker parity and
group/permission commands against disposable fixture directories.
It covers transient `503`, delayed Docker health, permanent refusal,
optional YASNO absence, dry-run, protected private-file modes, and
unsafe private-state permissions.

## Additional integrated source overlay acceptance

The genuine filtered source-tar.sh was executed against an isolated
source fixture containing deliberately different .env and Telegram
token data. After copy_source() and prepare_yasno_reader(), existing
configuration and token bytes were unchanged, a new YASNO script was
present, root group traversal was 0750/0710, and credentials kept
0600/0700. The integrated case passes with both UID root and nobody.

A separate **real Linux** cross-UID rehearsal on LXC110 used disposable
/var/tmp source and target trees, actual chgrp, chmod, and
runuser -u shelly-yasno. The actual forecast account successfully read
only the test YASNO script but could not read .env or the test Telegram
token. The original private file SHA256 values matched after source tar
extraction. The scratch directory was removed; the live project was
neither installed nor restarted.

This QA change is **not yet a new signed Git tag or OCI**. The
operational YASNO directory permission repair on LXC110 is effective
now, but a future installation should use the corrected installer.

## Invariants

- Only one live Shelly writer. The QA suite has no network access.
- No PHOTO enablement or Telegram test publications.
- Immutable signed `v0.5.22-hw` tag/digest are never moved.
- No false `offhost_backup_verified` assertion; the owner has accepted
  the one-NVMe physical failure domain.
- Rollback, cosign verification and writer fencing remain mandatory.
