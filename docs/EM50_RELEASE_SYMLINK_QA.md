# Release source symbolic-link containment

## Reproduced risk

The release script copies the candidate source tree through tar,
then checks excluded private regular files and generates a regular
file-only SHA256 manifest. A symbolic link from a public path to
.env, secrets or an absolute host path previously survived this
filter and appeared in the final reproducible public release TAR.
The symlink itself does not contain the secret file bytes, but may
enable unwanted references or unsafe source extraction later.

The canonical source-tar.sh already excludes symlinks;
the final public release path now independently rejects all
symlinks in the staged public tree before emitting any public
archive, and reports a generic error without printing sensitive
link destinations.

## Synthetic RED / GREEN checks

The final test_release.sh fixture first proves that a
normal clean source builds two byte-identical signed archives.
It then creates synthetic relative links to .env and secrets
inside a disposable private-source fixture. The previous release
accepted those links (RED). The corrected release refuses to
package the fixture, creates no public TAR and emits only a
generic failure (GREEN).

This is not a deployment or release authorization. Do not put
live private credentials in source worktrees or source archives.
The installed 0.5.13-hw and all production SQLite/Docker/Telegram
state remain unchanged.
