# v0.5.29-hw — narrow updater-only release review

Status: unsigned source candidate; not published or authorized to deploy.

Scope: the immutable v0.5.28-hw source baseline
59588e38b9a7ae59f14ac056451fe3c20f8a69d2 plus PR #454's public
receipt path preflight, PR #455's historical cutover review, and
PR #456's read-only three-phase electrical observation gate.
VERSION and RELEASE_VERSION move together; LIVE_VERSION is unchanged.

The report-worker stack (#457–#471) is deliberately not included.
The modified app.py and experimental recovery supervisor and transaction
tools require their own signed runtime compatibility and production
acceptance. The existing strict signed rollback input comparison
must not be relaxed to expedite either release.

Prior evidence: #454's real signed .27/.28 supervised QA with the
separately reviewed operator patch passed both early refusal and forward
and rollback trials. #456's window tests and read-only live observation
passed. Those results do not establish a signed .29 image or .29
supervised compatibility; exact signed artifacts and independent
QA must be checked after authorized publication.

The historical .28 cutover packet remains a record of that review, not
an authorization or an instruction to apply .29. For .29, build the
reproducible archive from the exact commit, verify its full contents
and strict monitor-input parity against .27 and .28, run CI, obtain
explicit owner approval for the exact release identity, and follow the
protected signing/publication workflow. A distinct production cutover
review must bind the signed source and OCI identities and address the
legacy DynamicUser receipt alias without changing old signed files.
Fresh electrical, database rescue, live PHOTO and exclusive operator
gates remain mandatory. No automatic installation is authorized.
