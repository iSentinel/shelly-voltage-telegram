# Prevent accidental SQLite history loss on rollback — QA

A source rollback is not necessarily a harmless software downgrade:
the existing scripts/rollback.sh restored both application files and
the exact SQLite snapshot captured before an older upgrade.

On November 8 2026 the selected pre-upgrade 0.5.13-hw snapshot from
November 6 contained 21,413 measurements, while the running bot had
more than 26,000. A direct unconfirmed restore could replace current
measurements, outages and the saved Telegram status message state with
older records.

The updated script requires explicit --confirm-db-replacement for
ANY real rollback that finds an existing SQLite file. A dry-run still
validates SHA256, source-tar safety and SQLite integrity, but prints
the warning and the confirmation required for actual execution.
Without confirmation, a real rollback fails BEFORE the rescue backup,
service stop, source replacement or SQLite restore.

## Inspection: safe and no writes

    sudo bash scripts/rollback.sh --help
    sudo bash scripts/rollback.sh --dry-run --no-start \
      --snapshot /var/backups/shelly-voltage/upgrades/20261106-085538-0.5.13-hw

## Risky operation, NEVER run without explicit approval

After verifying the up-to-date rescue backup, the version match,
snapshot integrity, lost-history exposure and production downtime,
an operator may explicitly approve replacement of SQLite history:

    sudo bash scripts/rollback.sh --no-start \
      --snapshot /var/backups/shelly-voltage/upgrades/20261106-085538-0.5.13-hw \
      --confirm-db-replacement

This is an EXAMPLE, not an executed production action. The script
still makes an online rescue backup first, so data can be recovered
separately. The confirmation option does NOT avoid data loss; it
requires an unambiguous operator decision to accept it.

There is no automated guarantee that older code can safely open a
newer SQLite schema, so preserving the current DB while restoring old
application code is NOT treated as automatically safe either.
Developers must separately verify schema compatibility before
introducing such a rollback mode.

## QA results

- Actual production dry-run: validated both SHA256 archives and SQLite
  snapshot, showed a clear data-replacement warning, no changes.
- Isolated rollback fixture: attempted live rollback without
  confirmation was BLOCKED before any target/rescue modification;
  explicit confirmed fixture rollback passed, as did privacy,
  snapshot-safety and rescue-history checks.
- The existing running 0.5.13-hw container, SQLite, Telegram and
  Shelly state were not touched.

This guard belongs in the unpublished EM-50 0.5.14-hw release chain,
not in the running 0.5.13-hw deployment until a reviewed release.
