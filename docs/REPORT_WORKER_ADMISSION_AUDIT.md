# Report Worker consolidated admission audit

2026-11-27. Review only; release and deployment NO-GO.

Common review base f35cfc4464684d0c6b15d27aec98016bf0ca1454
(qa/three-phase-cutover-window). Previous reviewed stack tip
473d97ee9516d760800e578285daf8b18861e498, #502 CI 8/8 PASS.
48 commits, 66 changed files, +13015/-117 lines at that tip
relative to the common base. 45 draft PRs (#457-471, #473-502)
are retained as individual engineering history. This consolidated
draft is an overlapping review, not a merge, squash or release.
The separate #472 narrow .29 head is NOT an ancestor of this stack.
Do not merge both tracks blindly. VERSION and RELEASE_VERSION in
the stack remain .28; no new release version is assigned.

## Evidence and remaining gates

| Scope | Evidence | Admission |
| --- | --- | --- |
| Report delivery reservation, HTTP deadlines, pause | Unit/CI and earlier isolated synthetic tests | Limited |
| Exact earlier unsigned runtime and same-DB transition | #487-488, same retained database, synthetic API | Limited |
| Durable operator and one-shot witness START | #489-490, isolated fault rehearsal | Limited |
| Fixed real report probe START, launch, acceptance, delivery | #491-493, unit/CI; complete integrated trial unavailable | NO-GO |
| Signed reference | .28 retained signed pair, not feature-capable | NO-GO for Report Worker |
| Incident and forward repair | Synthetic contracts/reviewers; no actual repair candidate | Not qualified |
| WAL rescue | Actual temporary SQLite API and seal tests #502 | Limited |
| Production | Healthy .27, legacy receipt alias unresolved | No deployment |
| Narrow .29 | Separate exact publication-only approval packet #472 | Owner approval pending |

Earlier blocked integrated commands were not repeated or bypassed.
Do not treat mock readiness, synthetic evidence or a historical
signed reference as end-to-end qualification of this exact stack.

## Observation boundary correction

Before persisting a runtime acceptance, revalidate the original
launch receipt (including restart count), immutable runtime profile
and prior history again after the readiness/phase checks. Capture
the full acceptance history after these checks, not before readiness.
Reject duplicate JSON fields in the consumed claim. Add direct
tests of the two retained probe file hashes, altered probe bytes
and unexpected probe-directory files, and modeled final launch/
profile drift. These checks narrow observation gaps but are not
one atomic SQLite/Docker/HTTP snapshot and cannot prevent all
noncooperating privileged changes. No actual container launch.

334 focused tests across 29 suites PASS, six new tests included.
Quality gate PASS, 378 formatted files. Test log on development
host: /var/tmp/svt-consolidated-tests.kXXhw5. Narrow archive
SHA256 06ac8440bcb4555f59a73f495b6fdffe0ad22f23c0e0de3d76d29998724cc497
reverified unchanged. #472 CI 8/8 PASS. No tag, signing, release,
merge, approval or production changes.

## Next decision

Prioritize review of the consolidated changes and exact integrated
qualification, not additional hypothetical incident layers. Keep
the narrow publication decision independent. If integration remains
unavailable, maintain explicit NO-GO and limit further work to
independent useful review/test corrections; do not fabricate
qualification or retry blocked execution through another route.

## Scheduled acknowledgement review correction (2026-11-27)

Exact consolidated #503 and narrow #472 CI 8/8 PASS. Review
identified that the scheduled HTTP response parser silently
accepted duplicate JSON fields (last value wins), and accepted
success with an error code or rejection with a result. A
contradictory 429 could therefore be classified as a definite
rejection and retried. Reject duplicate keys recursively,
nonstandard JSON constants, success carrying error_code, and
rejection carrying result. The worker exits with unknown outcome;
existing pending-intent semantics remain unchanged. Ordinary
Telegram status/command transport is unchanged.

12 scheduled HTTP tests PASS, including five new loopback tests.
A real synthetic contradictory 429 received exactly one request;
the SQLite pending marker survived and another delivery attempt
returned false without a second request. No actual Telegram calls.
116 additional tests in eight related suites PASS, total 128.
Quality gate PASS (378 files).

Runtime byte identity has changed:
- previous app.py SHA256 b9eea01ae7c5072a1bec2a0449c70640b58e3ebee10b42247a12baf9ede125b8
- corrected app.py SHA256 7052a862cff39338b26b6c253a4878050fcd8985c298158b0f26cfdb8b87a61d

The earlier unsigned local image, fixed START profile, source
scope archive and same-database smoke evidence refer to the
previous runtime. Do not claim they qualify this correction.
No image was rebuilt or retagged; the fixed historical image
identity is unchanged. A new exact candidate needs an explicit
source/build-input review, rebuilt image identity and integrated
qualification when available. Previously blocked integration
commands were not repeated or bypassed.

Narrow .29 archive SHA256 unchanged. No production changes,
release version assignment, merge, signing or authorization.

## Multiarchitecture OCI admission correction (2026-11-28)

Exact #507 and narrow #472 CI 8/8 PASS. Review of the shared OCI
release verifier found that the index platform descriptor was not
cross-checked against the downloaded OCI image config's OS and
architecture. Two architecture descriptors could also reference the
same manifest. The verifier now requires distinct image manifests
and matching Linux/architecture config fields before checking the
revision and per-platform SPDX/SLSA attestations.

Four new offline regression tests refuse shared manifest identity,
wrong architecture, wrong OS, and missing platform fields. Existing
valid dual-platform fixture now includes real config platform fields.
49 focused tests across four suites PASS; Ruff and quality gate PASS.
Initial quality run found a 42-line test fixture helper; split the
fixture config into its own helper and reran all checks successfully.

These are synthetic registry tests, not an actual multiarchitecture
build or signature verification. Runtime bytes and historical START
pins remain unchanged. The unsigned local candidate is amd64 only.
The full Dockerfile has a digest-pinned base but ranged Python
requirements and unpinned apt packages; a clean release build still
needs dependency/provenance and per-platform runtime qualification.
No integration trial, publication, signature, or production change.
The narrow .29 candidate remains a separate review scope; the new
OCI verifier correction has not been applied to its frozen bytes.

## Installed dependencies and independent signature binding (2026-11-28)

Exact #508 CI 9/9 and narrow #472 CI 8/8 PASS. The additional
independent verifier CI job concerns the historical configured
published release, not the new Report Worker candidate.

Read-only inspection of the saved, SHA256-verified amd64 image
(e37eeb2f2235acc17d5316c0a4789f4b629fe34cf86805bfdfabe7720c3571d3)
found 90 installed Debian packages and nine Python distributions.
CPython 3.13.18: loguru 0.8.0, python-dotenv 1.2.4, requests
2.34.2, Pillow 11.3.0; charset-normalizer 3.5.2, idna 3.20,
urllib3 2.8.0, certifi 2026.10.23; pip 26.2.2. Evaluated
PEP 508 markers for CPython/Linux x86_64 without extras: all four
direct requirements and four active transitive edges satisfy their
version specifiers; 68 conditional requirements are inactive.

Protected host evidence /var/tmp/svt-ack-dependency-review.bZRDvA:
- inventory.json SHA256 f8d5905bc8051b8a0c344516c7ca6db35ac8f81b9904b7eb61ba5d0796cb8df1
- review.json SHA256 d7a5bb4214dd45112bb7ea5f729ea49c6fcf4274610b849c6600bdc2744c9ec1
- dpkg status SHA256 f1eb7ad98c7c7d968c3a74670b8b52ee7c0c51ad0a35ef830a6528bc79fd3ee8

PASS_AMD64_INSTALLED_DEPENDENCY_METADATA_ONLY. This is not an
import test, pip check, vulnerability scan, package artifact lock
or arm64 qualification. Installed METADATA/RECORD hashes are not
original wheel hashes. APT uses mutable Debian trixie, updates and
security repositories; snapshot URLs are commented out. The
COPY-only local overlay inherits the base .28 revision and version
labels; it is not an exact release-provenance image for the new
source. No labels or runtime bytes were changed.

The independent published-image workflow previously supplied
Cosign identity and issuer but omitted explicit workflow SHA,
ref and repository constraints. It now requires all three,
consistent with the main signed-pair verifier. Three offline shell
regressions check exact CLI arguments, refusal on certificate
failure, and rejection of malformed commit before invocation.
52 focused tests across five suites and quality gate PASS.
An overlong test line was corrected before the successful rerun.

Next build admission requires verified per-platform dependency
artifact hashes, immutable APT snapshot or equivalent package
source with reviewed versions and checksums, actual full builds
for amd64/arm64, correct source revision labels, per-platform
inventory and provenance checks, and fresh integrated runtime
acceptance. Do not treat the installed amd64 inventory as a
release lock. No build, publication, signing, merge or production
mutation; earlier blocked integration was not repeated. Narrow
.29 approval packet remains frozen and separate.

## Strict OCI attestation schema admission (2026-11-28)

Exact #509 CI 9/9 and narrow #472 CI 8/8 PASS. Replaced
prefix-only SLSA acceptance with an explicit v0.2/v1 allowlist
and version-specific nonblank string buildType validation.
Reject duplicate JSON keys recursively, nonstandard numeric
constants and non-object JSON after registry digest verification.
Six new tests cover compatible v0.2, unsupported versions,
incorrect schema location, invalid buildType, exact-digest
ambiguous JSON rejection and valid strict JSON acceptance.
58 focused tests across five suites and quality gate PASS.

Read-only live GHCR check of the historical .28 immutable index
sha256:03e83600d633301c59bbe2fa247d7982d95133ad4ca96907828688711c3de249
and commit 59588e38b9a7ae59f14ac056451fe3c20f8a69d2 passed
revision, SPDX, SLSA and subject checks for both architectures.
This confirms historical metadata compatibility, not a new
signature check, full SLSA schema or material verification,
dependency artifact lock or runtime qualification. No new image
build, launch, signing, tag or production change. Frozen narrow
.29 archive SHA256 unchanged. Earlier blocked integrated trials
were not repeated. Next review exact build artifact acquisition
and per-platform provenance requirements; distinguish historical
metadata from new build evidence and keep deployment NO-GO.

## Cross-platform Python wheel artifact review (2026-11-28)

Exact #510 CI 9/9 and narrow #472 CI 8/8 PASS before this
review. The previous installed amd64 metadata inventory supplies
eight application distribution versions (pip is inherited from
the base and excluded from this application wheel lock).

Downloaded public PyPI wheel artifacts for CPython 3.13/Linux
amd64 and arm64: 16 platform entries, ten distinct files.
Selected non-yanked compatible wheels, verified actual size and
SHA256 against exact-version PyPI metadata, checked ZIP METADATA
name/version and WHEEL tags against the filename. Six packages
share pure-Python wheels; Pillow and charset-normalizer use
architecture-specific wheels. The native selected tags require
manylinux glibc 2.28 or earlier. The selection used the observed
amd64 glibc 2.41 ceiling; actual arm64 base compatibility remains
to be verified independently.

For each target, evaluated wheel Requires-Dist markers using
CPython 3.13.18 and the corresponding Linux machine, no extras:
four direct requirements, four active edges, 68 inactive
markers, eight reachable packages. The development host pip
25.1.1 independently downloaded both sets offline from the local
wheelhouse with exact hashes, only binary wheels and explicit
target tags. All eight resulting filenames and SHA256 values
per target matched the manifest. Pip cross-target marker
evaluation is not target execution; the independent metadata
closure check addresses this scope only.

Review-only files: docs/qa/report-worker-wheel-review/
- wheel-review.json SHA256 6f0691d0a48f319b6cdd8e6936f4cd20c4fac9da5e455b6517e6e1ed33b485f1
- closure-review.json SHA256 98f6ec94e4097221d594a52d73135f0d0ebcc1a259ba68cc36e4107716b98cbb
- amd64.requirements.txt SHA256 456cabaf5dfbcd2d2fb7e917b23524fd0a0ec01a0b7345232eb0c01f7734dacd
- arm64.requirements.txt SHA256 c16aea8d31ea135705ec3b5fbc8dc14327e3847b2c0d9a0d26f67a69433e1268

Original wheel bytes, release JSON and resolver logs retained
at /var/tmp/svt-wheel-review.dpEiTf (private host evidence).
These locks are candidate inputs, not proof of historically
installed wheel identities, package publisher signatures,
vulnerability clearance or a complete release dependency lock.
No installation, image build, container launch or production
change. Dockerfile and runtime requirements.txt remain unchanged.

Next: qualify base image platform identities and immutable
APT package sources, then review complete build inputs and
per-platform provenance. Full build, integrated acceptance,
signing and owner approval remain open. Earlier blocked trials
were not repeated. Frozen narrow .29 remains separate.
