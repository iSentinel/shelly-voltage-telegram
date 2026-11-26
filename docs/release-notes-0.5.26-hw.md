# 0.5.26-hw

**DRAFT QA candidate — NOT PUBLISHED / NOT SIGNED / NOT LIVE.**

This candidate is prepared for a future signed forward-only recovery
strategy. It does NOT authenticate the unsigned historical 0.5.25-hw
OCI image and does NOT authorize rolling the SQLite database backward.

Source-only QA additions include:

- Exact SHA signed-source/OCI repair preflight, monotonic new release
  identity, protected Docker mount layout and fail-closed decisions.
- Real synthetic WAL rescue, L1/L2/L3, Telegram PHOTO and pending
  send-intent SIGKILL acceptance.
- Real isolated LXC114 guest reboot and interrupted-receipt QA, without
  changing production or contacting Telegram.
- Reproducible source packaging with normalized modes independent of
  checkout and extraction umask.

Publication of this version must wait for completed CI, reproducible
source archive review, a genuine source Sigstore bundle, a signed
multiarch OCI index with exact Git SHA provenance, independent
verification and specific Owner Approval. A green QA build is not a
production release.
