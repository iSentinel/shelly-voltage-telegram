# 0.5.7-hw — Persistent Telegram voltage status editing

Voltage changes now edit one Telegram status post for a configurable 3, 5
or 10 minutes. The default is 10 minutes. Recovery and repeated low/high
transitions reuse that post until the original deadline; edits never extend
the deadline. After expiry, the next transition publishes a replacement
and then unpins and deletes the previous post.

The setting and deadline survive restarts through SQLite metadata.
Temporary edit failures keep the existing post. Sound cooldown remains a
separate setting, message edits stay silent, and quiet hours remain active.

Status pinning defaults to OFF and can be changed in administrator
`/settings`. Public status buttons are removed and legacy buttons are
cleaned up at startup.

Validation: project quality gate PASS, 158 unit tests PASS, Python
3.11/3.12/3.13 CI PASS, healthy/ready runtime, physical Pro EM-50 readings,
SQLite integrity, verified backup and Telegram diagnostics PASS.

This is a software release with runtime verification. The latest full
physical electrical-transition acceptance checkpoint remains 0.5.6-hw.
Physical Pro 3EM / 3CT63 acceptance still requires that hardware.

Publication status: prepared; no release tag or public release is created
by this preparation.
