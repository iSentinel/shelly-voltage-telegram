# Apartment-to-phase registry

This optional administrator feature associates apartment numbers with monitored
electrical phases. It does not measure or control an individual apartment.

## Telegram commands

The administrator sends commands in the configured control chat. The bot sends
the list or confirmation to the administrator's private Telegram chat. Start a
private conversation with the bot before using these commands, or Telegram may
reject the private reply.

```text
/apartments
/apartments list
/apartments list L1
/apartments add HouseA 12 L1
/apartments add HouseA 13 L1
/apartments add HouseA 12 L2
/apartments remove HouseA 13
```

Use `L` or `L1` for a single-phase Pro EM-50. The command refuses to assign
apartments to L2/L3 unless those phases are configured in the monitor.
Apartment numbers must be between 1 and 99999. Building identifiers accept
1-32 Latin letters, digits, underscores and hyphens, without spaces.

`add` also moves an existing apartment to another phase without creating
duplicates. `list` shows up to 100 assignments and reports the total.
`remove` is safe to repeat. All commands require a configured Telegram
administrator ID. A regular user never receives the apartment list.

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
