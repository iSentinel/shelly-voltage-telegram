# YASNO-aware private Telegram preview — opt-in QA

The independent direct YASNO schedule adapter is available at
`yasno_schedule.py` (see `docs/YASNO_KYIV_PROVIDER_QA.md`).
This QA stage allows the administrator-only `/widget_preview`
formatter to use a bounded local copy of the YASNO JSON for
region 25 / DSO 902, group 10.1.

## Opt-in environment

```shell
YASNO_CACHE_FILE=/absolute/path/to/kyiv-dtek-schedule.json
YASNO_GROUP=10.1
```

Both variables must be explicitly configured. The file must be
regular, not a symlink, no larger than 1 MiB, and contain a
well-formed schedule for **today in Europe/Kyiv**. Publication
time may be at most 36 hours old. The local capture must be
no older than 2 hours. A successful cache lookup says nothing
about whether the file was truly fetched from the claimed
provider: mtime is not a secure provenance record.

This stage does **not** configure automatic fetching or
publish widgets. The production command handler does not load
this QA module. A future deployment requires controlled
cache retrieval with durable origin metadata, file ownership,
atomic replacement and source attestation.

When a validated YASNO cache is present, it takes precedence
over the existing opt-in community cache. If YASNO fails
validation, the separate community cache may be considered;
otherwise the view only contains fresh measured Shelly data.
The source appears as `YASNO · DTEK Kyiv`, not as
`outage-data-ua`. The displayed schedule is a forecast, not
a measured power state.

Safety: no requests from the preview handler; the direct
YASNO API fetch remains an explicit QA CLI operation. The
`/widget_preview` command stays limited to authorized
private administrators, sends only after an actual command
and never edits channel messages. Tests mock all sends.

## Read-only QA

```bash
python3 yasno_schedule.py --help
python3 yasno_schedule.py --fetch --group 10.1
PYTHONPATH=.:tests python3 -m unittest tests/test_yasno_widget_preview.py -v
PYTHONPATH=.:tests python3 -m unittest discover -s tests -q
scripts/quality.sh
```

Local combined acceptance 2026-11-08: 1045/1045 tests PASS,
Ruff and quality gate PASS. Telegram QuickJS and actual
recipient-visible tests remain mandatory release gates.


## Provenance-labelled cache (next QA stage)

Use `yasno_cache.py --output /existing/directory/yasno.json`
to fetch and validate YASNO from the pinned public HTTPS
endpoint, then atomically install a private `0600` cache.
The envelope includes the pinned URL, Kyiv region 25, DTEK
operator 902, capture timestamp and a SHA-256 checksum to
detect accidental corruption. SHA-256 is **not** a digital
signature and does not authenticate an attacker-controlled
local file; protect the writer account and filesystem.

```shell
python3 yasno_cache.py --help
python3 yasno_cache.py --output /existing/directory/yasno.json --group 10.1
```

An explicitly configured `YASNO_TRUSTED_CACHE_FILE` takes
priority over the legacy `YASNO_CACHE_FILE`. If the trusted
cache is invalid, the preview does **not** silently downgrade
to the legacy raw file. It can still use a separately verified
community source, or show only the fresh Shelly measurements.

This writer has no scheduled service, no Telegram sender and
no production deployment. Before activation, secure the
destination directory and configure a bounded refresh cadence.
