# 0.5.28-hw

This release updates signed installation and recovery for Shelly Voltage
Telegram.

- An installation already managed by the single-writer supervisor can
  upgrade to a new signed source archive and pinned container image. It
  keeps the current SQLite database and the same writer lock.
- If the update stops before the candidate can write, recovery restores
  the previous signed supervisor. After candidate startup, recovery
  resumes the candidate with its current SQLite history.
- The scheduled release checker uses a dedicated systemd identity.
- Legacy recovery verifies the current SQLite history before it restores
  image settings. If publication of the installed-version receipt
  fails, the receipt can be synchronized independently.

Use the reviewed [signed update procedure](UPDATES.md#switch-the-signed-source-and-oci-together).
Verify the source Sigstore bundle and pinned image against the same
release commit before installation.
