# Apartment-to-phase registry

This optional administrator feature associates apartment numbers with monitored
electrical phases. It does not measure or control an individual apartment.

## Telegram commands

The administrator sends commands in the configured control chat. The bot sends
the list or confirmation to the administrator's private Telegram chat. The
private list includes inline buttons for All and each configured phase. Start a
private conversation with the bot before using these commands, or Telegram may
reject the private reply.

```text
/apartments
/apartments list
/apartments list L1
/apartments summary
/apartments add HouseA 12 L1
/apartments add HouseA 13 L1
/apartments range HouseA 20 40 L1
/apartments place HouseA 12 2 5
/apartments building HouseA
/apartments entrance HouseA 2
/apartments floor HouseA 2 5
/apartments add HouseA 12 L2
/apartments remove HouseA 13
```

Use `L` or `L1` for a single-phase Pro EM-50. The command refuses to assign
apartments to L2/L3 unless those phases are configured in the monitor.
The `range` command accepts up to 200 apartment numbers in one transaction.
Apartment numbers must be between 1 and 99999. Building identifiers accept
1-32 Latin letters, digits, underscores and hyphens, without spaces.

`summary` reports aggregate apartment counts by building and phase without
listing apartment numbers. It is also administrator-only.

`building`, `entrance` and `floor` return read-only filtered lists in the
administrator's private chat. SQL queries use bound parameters and return at
most 100 entries with the total count.

`place` sets entrance (1-100) and floor (0-200) for an existing assignment.
It does not create a new apartment. Existing rows migrate in place, with
entrance and floor initially unset. Moving a phase keeps both fields.

`add` also moves an existing apartment to another phase without creating
duplicates. `list` shows up to 100 assignments and reports the total.
`remove` is safe to repeat. All commands require a configured Telegram
administrator ID. A regular user never receives the apartment list.


## CSV import (administrator CLI)

Import a five-column UTF-8 CSV with this exact header:

```csv
building,apartment,phase,entrance,floor
HouseA,12,L1,2,5
HouseA,13,L1,,
```

The importer validates the entire file before writing anything. By default
it is a **dry-run**, which leaves the SQLite database unchanged. An explicit
`--apply` performs one transaction after validation. Duplicate apartments,
unknown phases, incomplete locations and more than 1000 rows are rejected.
Blank entrance and floor values preserve existing location details.

```bash
python3 scripts/import_apartments.py --help
python3 scripts/import_apartments.py \
  --csv /tmp/apartments.csv --db /path/to/voltage.sqlite3
python3 scripts/import_apartments.py \
  --csv /tmp/apartments.csv --db /path/to/voltage.sqlite3 --apply
```

Run `--apply` only after backing up the database. For a three-phase meter,
use `--allowed-phases L1,L2,L3` only if all three phases are actually
configured. The CLI does not contact Shelly or Telegram.

## Storage and safety

Mappings are held in the existing SQLite database in a separate
`apartment_phases` table, created when an administrator first invokes the
feature. No existing measurements or outages are modified. The registry uses
one apartment assignment per (building, apartment) pair.

Back up the database before upgrading. Existing SQLite backups include
this table automatically. The feature does not send unsolicited apartment
notifications and does not publish apartment lists into public groups.

**Important:** A shared-phase voltage reading cannot determine whether an
individual apartment has electricity. Tripped apartment breakers, local
wiring faults, or other downstream problems require separate sensors.

## Validation

Run the full quality gate and tests in an isolated checkout:

```bash
./scripts/quality.sh
python -m unittest discover -s tests -p 'test_*.py' -q
```

No electrical control or artificial outages are needed.
