# Issue422: reject unsafe old signed source TAR members

Date: 2026-11-19. State: source-only QA; not deployed.

The signed rollback pair validator previously checked only archive
SHA256 sidecar and VERSION/RELEASE_VERSION members. A checksummed
source TAR could still contain unsafe extraction entries even when its
version matched the old signed OCI receipt.

The read-only scripts/signed-rollback-pair-preflight.py now scans all
TAR headers before accepting the paired source/OCI identity. It
refuses absolute paths, parent-directory traversal, symbolic and hard
links, duplicate normalized member paths, unexpected member types,
setuid/setgid entries and excessive member count/expanded sizes.

It does NOT extract any TAR payload to the filesystem, restore the
SQLite database, change the running Docker container, or claim that
a source SHA256 sidecar cryptographically signs the source. Cosign
verification of the old OCI digest and exact old/new source pairing
remain independently necessary.

Isolated LXC114 acceptance: 31/31 focused source/OCI pair tests PASS,
including five new hostile TAR tests (traversal, absolute path,
symlink, canonical duplicate, oversized header). 177/177 selected
joined source-only regression tests PASS on nonroot networkless QA
Docker with read-only source mount. Ruff0.14.2 and maintainability
checks PASS.

Actual updater/rollback remain blocked pending a single signed
source+OCI version transaction, full newest WAL/PHOTO protection,
crash-point acceptance, exact HEAD CI and separate Owner Approval.
