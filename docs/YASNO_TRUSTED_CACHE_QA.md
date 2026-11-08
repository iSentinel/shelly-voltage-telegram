# Atomic YASNO cache — private QA workflow

The optional `yasno_cache.py` utility retrieves today's
Kyiv/DTEK operator forecast from the single pinned HTTPS YASNO
endpoint and validates the entire 48-half-hour-group plan
before installing any cache file.

## One-time manual QA test

```bash
python3 yasno_cache.py --help
python3 yasno_cache.py --output /tmp/shelly-yasno-trusted-20261108.json --group 10.1
PYTHONPATH=.:tests python3 -m unittest tests/test_yasno_cache.py -v
```

The cache is installed by a temporary file in the same existing
directory and atomically replaced only after validation. It is
mode 0600; the writer fsyncs both the new file and directory.
The reader rejects symlinks, unsafe writable files, schema
mismatches and corrupted payloads. Failed updates preserve
the previous cache. SHA-256 detects corruption, **not**
cryptographic authenticity of an attacker-controlled cache.

The envelope metadata contains:

- Exact pinned YASNO HTTPS endpoint and region/operator IDs
- UTC retrieval timestamp from the direct successful request
- SHA-256 hash of the serialized payload
- Original YASNO public JSON response

## Preview opt-in (QA only)

```shell
YASNO_TRUSTED_CACHE_FILE=/tmp/shelly-yasno-trusted-20261108.json
YASNO_GROUP=10.1
```

If the trusted cache is configured, any invalid, modified or
expired trusted cache is omitted; an older untrusted
`YASNO_CACHE_FILE` is **not** accepted as a fallback.
The independent community cache can still be used with its
own separate attribution. Otherwise only recent Shelly
measurements are displayed.

No Telegram command causes a YASNO network request. The
command remains private/admin-only and is not deployed.
Production polling, Telegram channel, SQLite and Shelly
remain untouched.

Before release, provision a protected dedicated cache
directory, a restricted noninteractive fetch service with
retry/backoff and documented refresh cadence; test failure
and stale-cache alerting. This stage only validates the
atomic cache format and a manual **QA** fetch.
