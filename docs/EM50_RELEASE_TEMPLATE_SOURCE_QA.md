# Validate the public `.env.example` template before staging

The final reproducible-release builder excludes environment files from
its initial tar stream, then copies `.env.example` back with `cp`.
Ordinary `cp` follows a source symbolic link. Therefore, rejecting
symlinks only in the staged public tree does **not** establish that the
template's source was a standalone public file.

Before staging, the release builder now requires a regular,
non-symbolic-link `.env.example` with exactly one filesystem link.
A missing or linked template is rejected with a generic failure
before an output archive is created.

The synthetic fixture replaces the local template with a symbolic
link to a harmless README, and separately with a hard link to that
README. Both must be rejected, even though the targets contain no
secrets. The legitimate independent template must still produce a
byte-reproducible release and pass privacy and install fixtures.

This is **not** a claim that private data was previously shipped.
No real secrets or production databases were used. No release
installation, Telegram command, Shelly control, or service restart
is authorized by these tests.
