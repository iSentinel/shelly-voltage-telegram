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
