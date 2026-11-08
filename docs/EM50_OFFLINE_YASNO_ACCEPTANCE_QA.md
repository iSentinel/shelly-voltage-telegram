# EM-50 offline + YASNO Telegram status acceptance (QA)

This is an isolated, source-only regression suite for the unpublished
EM-50 0.5.14-hw candidate. Production 0.5.13-hw, Shelly hardware,
Telegram channel and live SQLite are not changed.

The live outage observed on 2026-11-08 revealed an important display
constraint: the bot was reachable on Proxmox backup power, but the
Shelly EM-50 RPC endpoint was not. Its latest preserved measurement
was 229.3 V / 50 Hz and more than 2 hours old. The monitoring API
reported connectivity=offline, /ready HTTP 503. The SQLite incident
table had one active L1 outage and no new measured values after
communication was lost. The last voltage must NOT be shown as current.

tests/test_em50_offline_yasno_acceptance.py tests the actual
channel_status_text function with a private disposable database and
mocked Telegram transport. It never sends, edits or pins a message.

The eight scenarios include:
- Offline + an independently source-labelled, current YASNO group 10.1
  planned schedule. The old Shelly voltage/frequency are never
  displayed as currently measured; the schedule is explicitly labelled
  as a forecast rather than a physical reading.
- Russian, Ukrainian and English text safety.
- Expired (>2 hours), missing or corrupt YASNO cache: no old planned
  outage windows can be shown as current.
- Disabled YASNO feature: preserve the original status byte-for-byte.
- Kyiv midnight rollover: yesterday's schedule cannot be today's.
- All render paths are free of Telegram and SQLite mutations.

The YASNO schedule is a *forecast* even when it overlaps a physical
offline interval. Loss of contact with Shelly does NOT independently
establish whether the mains is down, though the operator has reported
the local power outage.

Run on a QA worktree (no production deployment):

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_em50_offline_yasno_acceptance.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh
    bash tests/test_release.sh

The release remains blocked by the dedicated shelly-yasno service
account, protected reader mount, review of the broad integration
stack and explicit signed release/rollback authorization.
Private Telegram send/edit visibility was confirmed by the operator.
