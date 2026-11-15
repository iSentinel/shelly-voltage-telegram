# Issue #356: public deployed-version commit-boundary QA

**QA-only. Review-only. Production remains NO_GO.**

This isolated fault model addresses the public/private release-metadata
consistency problem from issue #356. It does not install a release, stop
a writer, restore SQLite, contact Shelly or touch Telegram.

## Safety

- The CLI defaults to inert dry-run.
- Mutation mode runs only as root on exact hostname
  `shelly-docker-reboot-qa` with loopback as its sole network interface.
- Every written file is invented and lives in a fresh mode-0700
  `/var/tmp/svt-public-metadata-qa-*` directory on offline LXC114.
- The tested publisher uses mode 0600, file fsync, atomic os.replace,
  directory fsync and cleanup of a leftover private temporary file.
- No live metadata, token, SQLite database or container is accessed.

## Reproduction

```bash
python3 scripts/disposable-public-metadata-failure-qa.py --dry-run
python3 -m unittest -v tests/test_public_metadata_failure_qa.py

# Explicit offline LXC114 only:
python3 scripts/disposable-public-metadata-failure-qa.py \
  --run-fixtures \
  --confirm ISOLATED_PUBLIC_METADATA_FAULT_QA_ONLY
```

Both `-h` and `--help` are supported. Exit 0 means the explicitly
selected synthetic checks passed, 2 means wrong confirmation,
3 means a failed host gate, assertion or synthetic I/O action.

## Isolated acceptance (2026-11-15)

17/17 scenario assertions PASS and 14/14 separate Python unit tests
PASS on networkless LXC114. Source and tests compile. All Python
functions are at most 40 lines and source lines are at most 79 chars.

The model checks missing private commit, pending still present,
pending cleared with stale public state, missing/corrupt public state,
absent synthetic signature trust, mismatched running reference or
image ID, and full consistency.

Injected failures after temp-file fsync, after os.replace, and after
directory fsync produce either the exact previous JSON or the exact
complete new JSON. In all cases an injected failure raises, rather
than claiming that the operation succeeded. Temporary files are
removed, and successful publication has mode 0600.

The comparison uses **invented** v0.5.20/v0.5.21 metadata and fake image
SHA values. The `verified` input is a test-model flag. It does NOT
cryptographically verify Sigstore signatures, OCI manifests or source
identity. It also does not guarantee real hardware crash durability.

## Design for a future owner-approved implementation

1. Keep a single authoritative signed running image identity and
   explicit pending/committed publication transaction status.
2. Publish the public version atomically with both fsync barriers.
3. Never report UPDATE PASS after a publication exception without
   a subsequent reconciled, independently checked status.
4. Only reconcile after a whole-writer B/A owner fence, exact signed
   image/source check and complete CURRENT SQLite A/B restore proof.
5. Exercise real failpoints on a sacrificial host, including physical
   power loss, independently from live production or Telegram.
6. Do not roll back valid newer SQLite history merely to repair a
   stale public-status file.

This PR deliberately does NOT change `scripts/apply-update.sh`, systemd,
Docker restart policy, public Telegram pins or production metadata.
