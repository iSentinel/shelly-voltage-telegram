# YASNO Linux identity handoff acceptance — QA only

This is an isolated identity test for the unpublished EM-50 0.5.14-hw
candidate. It does not install systemd units, edit a live Docker
Compose stack, send Telegram messages, or write production SQLite.

The test uses a fresh private /tmp directory and synthetic valid
YASNO-format data for the current Kyiv date. It avoids all provider
network calls. It then exercises the same CLI entrypoints as the
proposed systemd services with actual numeric Linux UIDs:

- Producer UID:GID 10002:10002 owns mode 0700 cache folder and mode
  0600 source. Invokes the refresh CLI with a verified fresh cache,
  which must SKIP the network fetch.
- Exporter UID 0 with bounded CAP_CHOWN + CAP_DAC_OVERRIDE reads the
  private source and atomically writes a mode 0640 file for GID 10001
  inside a mode 0750 root:10001 directory.
- Recipient UID:GID 10001:10001 can read the exported cache, cannot
  write it or its directory, and cannot access the private source.

The fixture and its files are deleted automatically at test end.
The test is skipped on unprivileged CI workers that cannot change
Linux identity. Independent unit tests, Docker networkless smoke,
and the static systemd verifier still run in normal CI.

## Run on the QA host only

    cd /opt/shelly-voltage-em50-identity-acceptance-20261108
    PYTHONPATH=.:tests python3 -m unittest tests/test_yasno_role_isolation.py -v
    PYTHONPATH=.:tests python3 -m unittest discover -s tests -q
    scripts/quality.sh

The test does not prove a systemd service is installed or starts
successfully inside the actual LXC. No production unit activation
has occurred. That verification still requires an explicitly
reviewed one-time installation/rollout. This test does prove that
ordinary Linux file permissions and the bounded exporter capabilities
are sufficient under current QA host security constraints.

Remaining gates before channel update: private recipient-visible
Telegram send/edit observation, operator-approved release promotion,
verified production bind mount and rollback acceptance.
