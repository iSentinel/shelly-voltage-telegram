# Snapshot-directory ancestry checks in EM-50 rollback

## Reproduced risk

The rollback validator previously rejected a symlink only when
the final snapshot directory entry itself was linked. A symlink
in an ancestor component could redirect source and SQLite archive
reads while checksum validation still succeeded for that redirected
location. A synthetic snapshot with valid checksums proved RED:
rollback --dry-run accepted the redirected parent.

## Fail-closed fix

The rollback script now validates the entire ancestor chain for both
the automatically selected snapshot root and the explicitly selected
snapshot path. Any symlink in the directory ancestry causes a generic
error code 3 before source archive reads, service stop, rescue copy,
source replacement or SQLite replacement.

The regression fixture uses a temporary symlinked parent, synthetic
SQLite and a synthetic source archive. Expected result:
ROLLBACK_LINKED_SNAPSHOT_PARENT=REJECTED, no target or database change.

## Release scope

Tests run only against temporary fixtures; no production rollback,
container stop, backup capture, Telegram send/edit or Shelly action.
The 0.5.14-hw release remains blocked by current backup parity,
independent review, visual signoff and signed operator authorization.
