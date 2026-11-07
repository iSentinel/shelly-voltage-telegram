# Shelly network reachability preflight (read-only)

A separate connectivity loss is not evidence that mains power went off.
`scripts/shelly-network-preflight.py` differentiates a DNS problem,
TCP timeout, unreachable host, and refused TCP port using the Shelly
address already configured in the existing monitor environment.

It uses only `getaddrinfo` and a bounded TCP `connect()`. It does
not invoke RPC methods, change Shelly, read or write SQLite, manage
Docker, or send, edit, pin or delete Telegram messages. Reports
deliberately **omit host/IP, MAC, username and credentials**.
PASS means the configured TCP port is reachable, **not** that Shelly
RPC works or that the electricity supply is normal.

```bash
python scripts/shelly-network-preflight.py --help
python scripts/shelly-network-preflight.py --dry-run --json
python scripts/shelly-network-preflight.py --timeout 2 --json
```

The `--timeout` value is restricted to 0.1–5 seconds per DNS
candidate, and at most four candidate sockets are tested.
Exit code 0 means PASS or safe dry-run, 2 means FAIL.

When running QA source against an existing production Docker container
without installing any files:

```bash
docker exec -i shelly-voltage-bot python - --timeout 2 --json \
  < scripts/shelly-network-preflight.py
```

An `EHOSTUNREACH` or LXC neighbor state `INCOMPLETE` suggests
a local-network reachability issue. It does **not** prove that the
device is powered off. A changing IP, switch, VLAN, Ethernet cable,
or device restart can all cause similar symptoms. Record operator
evidence before identifying a root cause.

Use a separate natural recovery baseline and fresh real measurements
for each new incident. Never manufacture voltage-zero measurements,
close incidents manually, or change production thresholds as a test.
