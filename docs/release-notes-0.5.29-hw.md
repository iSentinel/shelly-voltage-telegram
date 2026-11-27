# 0.5.29-hw

Narrow updater-only release candidate. Publication and deployment
require separate review; this document does not certify either.

- Reject unsafe public receipt paths before signed supervised cutover,
  including the legacy DynamicUser receipt alias.
- Provide a read-only 15-minute three-phase electrical observation
  gate, including sample coverage, voltage, frequency and outage checks.
- Preserve the existing monitor source, database format, Docker build
  inputs and strict signed rollback policy.
- Exclude the experimental scheduled-report runtime and its recovery
  transaction stack from this release.

The prior signed release is v0.5.28-hw. The last observed production
runtime remains v0.5.27-hw. The historical hardware acceptance marker
LIVE_VERSION remains 0.5.6-hw. Neither source publication nor the
electrical observation gate authorizes production activation.
