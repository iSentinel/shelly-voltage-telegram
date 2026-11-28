# Base image and Debian font artifact review

2026-11-28. QA evidence only; not a release lock.

Pinned Docker Hub base index:
sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb

Verified actual registry index, manifests and configs by digest.
Both configs advertise Linux, expected architecture and Python
3.13.18; four layers per platform. Layer bytes and actual ARM64
runtime glibc were not inspected in this review.

| Platform | Manifest SHA256 | Config SHA256 |
| --- | --- | --- |
| amd64 | aaf08c5490d52cd92bc27142a5215b1e47355a5eaf9adfad2a0fbc022d0b5490 | bdfc738a8a869292ec1b01f1b8e620db533e09d1fe68fe980a7118b22dae9cec |
| arm64 | 7b58e4266b99c869d2225c050345f7c7fec255d9e3baeca19ea7de9411411c91 | 03e07c1a31b1330bcca830eb9ca263608aef404b6581281e3e4b1ee401dcf4d4 |

Debian snapshot 20261027T000000Z (the timestamp in the historical
image's commented source) supplied three InRelease documents.
All three passed gpgv with the development host's installed Debian
archive keyring.

| Suite | InRelease SHA256 |
| --- | --- |
| trixie | 0584fba32e13e0ab8285fb16c27adea1ec03a73669c18702821094fd6ca86675 |
| trixie-updates | 3b036a4df7cfa529e65f718aaec07c6d126ccdfe6782db0df815d5d06ec7ea76 |
| trixie-security | dfab8e28ea58bbf3bc3840bbd099d3e52ed44f72e447220e0b1b7c12ab0c0baf |

The updates and security metadata expired on 2026-11-02.
Signature validity is not current freshness. No APT validity
check was disabled, and no host source or package was changed.

Downloaded both trixie main binary Packages.xz indexes and
verified exact length and SHA256 against the signed InRelease.
Both indexes list identical architecture-independent DEBs:
fonts-inter 4.1+ds-1 and fonts-noto-core 20201225-2.
Downloaded the two unique artifacts, verified exact size/hash
against those indexes and read package/version/architecture with
dpkg-deb -f (no installation or maintainer script execution).
The adjacent JSON records exact index hashes, package hashes,
paths and declared dependency fields.

Protected raw snapshot evidence:
 /var/tmp/svt-apt-snapshot-review.4DMrHi
font-index-review.json SHA256:
150cd039a7813d27b34bdbe7fb02166839ed1bd2ffde592cb172d85215f909c7

No complete APT dependency closure, current security assessment,
actual ARM64 base layer inventory or full reproducible build is
claimed. Next qualify complete base/package dependencies and
define an explicit reviewed snapshot freshness policy; do not
silently disable expiry checks. No release, signing, deployment
or repetition of blocked integrated trials.

## Base layer and declared font dependency closure

2026-11-28. Exact #512 and narrow #472 CI 8/8 PASS.

Downloaded all four compressed layers per platform from the
pinned Docker Hub manifests. Verified compressed lengths and
SHA256 and independently hashed the decompressed streams against
all eight config DiffIDs. Inspected dpkg status without extracting
or executing image contents. No relevant status whiteout was found.
Each base contains 87 installed Debian packages, with libc6
2.41-12+deb13u4 on both architectures. This confirms the base
package metadata satisfies the selected wheels' manylinux glibc
ceiling, not actual native imports or loader compatibility.

Base inventory SHA256:
07ec1919b3837db01452c6c56aa6ca0a69d16c6243782135d2d7ebf3ae47d555
Protected downloaded layer evidence:
 /var/tmp/svt-base-layer-review.1kaRJS

The historical signed trixie main indexes identify three font
packages: fonts-inter 4.1+ds-1, fonts-noto-core 20201225-2 and
fonts-noto-mono 20201225-2. The only declared Depends edge is
core -> mono. No Pre-Depends or Recommends in these records.
An initial conservative assertion rejected the presence of Breaks
fields; reviewed the actual five clauses per target against the
combined base and font versions using Debian version comparison.
No applicable conflict was found. All three exact DEBs match the
signed indexes' size and SHA256. New mono artifact SHA256:
7636f002caeb4a4082f69e2d9b8eb89da6c50b9c67f55db84e68638049b06006

Expected amd64 package name/version/architecture set (87 + 3)
exactly matches all 90 packages in the historical local image
inventory. No extras or omissions. Expected arm64 count is also
90, but no new arm64 runtime image has been built. This is the
declared font closure on the exact base, not a full APT solver
transaction, reverse dependency review, maintainer-script
execution or security upgrade qualification.

Font closure SHA256:
abc197289d5fe087675ca4c08702fe6df324b8445612ef0fd36b510ab6affe06

## Snapshot freshness decision boundary

Independently retrieved snapshot 20261127T000000Z on 2026-11-28.
Three InRelease signatures verified with the host Debian keyring.
Stable trixie has no Valid-Until; updates and security are
unexpired at observation, until 2026-12-03 20:15:07 UTC and
21:03:36 UTC respectively. This newer snapshot is a candidate
for further review, not an approved build input set.

Freshness evidence SHA256:
e353544439acde70de5875086cc2a78004b872195100b076e32aad85d67c3a32
Raw metadata and gpgv logs:
 /var/tmp/svt-apt-freshness-review.0jqKdx

Proposed admission rules: select an explicit immutable timestamp
and trusted archive keyring; verify signed codename, dates and
architecture/index hashes; require unexpired updates/security
metadata at build admission; for stable metadata without expiry,
require separately reviewed recency and upstream release identity.
Recheck freshness at use, reject missing or expired required
metadata, and do not disable validity checks to obtain a PASS.
Historical expired metadata can support archival artifact identity
only, not new release freshness. New snapshot index/package
resolution, comparison of base versions with security updates,
dependency artifact provenance, native execution and full release
build remain required. No vulnerability-clearance claim. No APT
configuration, package installation, image build, deployment,
or retry of blocked integrated qualification.

## Fresh snapshot version gaps and candidate upgrade artifacts

2026-11-28. Exact #513 and narrow #472 CI 8/8 PASS.
Reverified all three InRelease signatures and expiry and downloaded
six main package indexes for amd64/arm64 from snapshot
20261127T000000Z; all sizes and SHA256 match the signed indexes.

Compared the 87 exact base packages and three fonts per target
with the snapshot using Debian version ordering. Both targets:
77 current versions, 13 upgrades available, six packages with
newer versions in trixie-security, zero missing package names,
83 exact old versions still present. These are version gaps,
not CVE counts or proof of vulnerability clearance.

The six security-suite gaps are apt, libapt-pkg7.0, libzstd1,
login.defs, passwd and zlib1g. Seven additional upgrades are
base-files, libc-bin, libc6, libssl3t64, openssl,
openssl-provider-legacy and tzdata.

Downloaded the newest selected versions of all 13 packages
and the three font packages on each architecture: 32 platform
entries and 27 distinct DEB files. Verified actual sizes and
SHA256 against the signed index records, and Package, Version,
Architecture from dpkg-deb control metadata. No package scripts
executed and no installation.

Read the exact dpkg status from the previously verified base
layers, rechecked its hash, and overlaid the selected DEB
control metadata in memory. Using apt_pkg Debian relationship
parsing and version comparisons (including virtual Provides
and alternative dependencies), reviewed the proposed 90-package
final set on each architecture. All declared Depends and
Pre-Depends groups are satisfied, no applicable Breaks or
Conflicts found. AMD64: 144 Depends, 69 Pre-Depends, 131 Breaks,
23 Conflicts. ARM64: 144, 69, 134, 23 respectively. This is a
static final-set relationship review, not an APT solver plan,
package installation order, maintainer-script or runtime test.

Evidence:
- version-gap-review.json SHA256 aca09e6d3ff6c6c6ae076041cab09c866f66bf0ad3fab6449ef2ddfb0ed381ad
- upgrade-artifact-review.json SHA256 bc9ac82ce083b7eb559be0e3f11bd8323b5a57471d2032d8f189defa4a21fb83
- static-relationship-review.json SHA256 02f5ff815e4efccf890de4fd33bf8c2e86f260ef4b8b37dde9dc807f9a6c502b

Protected raw artifacts: /var/tmp/svt-apt-upgrade-artifacts.93liz9
The previous base is not current against this snapshot. The
reviewed newer set is a proposed build input, not an approved
release lock. Next review an isolated APT transaction plan
and complete immutable build inputs, freshness at use, native
multiarch runtime and security assessment. Dockerfile and
production remain unchanged. Earlier blocked integrated trials
were not repeated. Narrow .29 approval remains separate.

## Isolated APT transaction simulation

2026-11-28. Exact #514 and narrow #472 CI 8/8 PASS.
Host apt-get 3.0.4 simulated the exact local DEB inputs using
separate temporary APT configuration, empty sources and package
lists, and copies of the original base dpkg status for each
target. Explicit target architecture, no-download, no-remove,
no-install-recommends and simulation mode. No host package
installation, sources modification or maintainer-script execution.

Both targets independently resolved 13 upgrades and three new
font packages, zero removals. Parsed all 16 Inst and 16 Conf
records per target and matched package names, versions and
architectures against the verified artifact manifest. Base
status hashes remained unchanged. Negative simulation omitting
fonts-noto-mono refused on both targets with exit 100.

Transaction evidence SHA256:
1e0249e8950fcf64abc4593c1a53e2fc678269a4b1469470a3af4b0470a3fa58
Original isolated simulation directories and logs:
 /var/tmp/svt-apt-plan-review.S9KTaV
Retained positive and negative logs adjacent to this README.

build-input-review.json combines the reviewed base layer,
Debian artifact, Python wheel and evidence hashes. It is a
review-only input index, not a complete release lock or a
build authorization. No actual APT transaction, native ARM64
execution, full build, runtime acceptance or vulnerability
clearance is claimed. Host APT 3.0.4 differs from the base's
initial 3.0.3; solver compatibility inside each actual image
remains to be checked. Freshness must be rechecked at use.
No Dockerfile or production change; earlier blocked integrated
trials were not repeated.

Next: design the full build input consumption and provenance
review using the verified byte sets, recheck freshness, and
qualify native execution and full build before signing.

## Deterministic platform-specific build input staging

2026-11-28. Exact #515 and narrow #472 CI 8/8 PASS.
New QA CLI scripts/qa-stage-report-build-inputs.py stages local
reviewed bytes only, without Docker, package installation or
network access. It binds the fixed build-input manifest and
source-scope manifest hashes, verifies all ten linked evidence
files, checks the exact 29 original build inputs and retains
26 runtime files. Each platform bundle includes 16 DEBs, eight
wheels, a hash-pinned requirements lock and an input manifest.
The base layer identities are recorded, not embedded.

Usage on the development host (output must not already exist):

    python3 scripts/qa-stage-report-build-inputs.py --arch amd64 \
        --scope /var/tmp/svt-report-ack-scope.5Yic9k/scope-review.json \
        --debs /var/tmp/svt-apt-upgrade-artifacts.93liz9 \
        --wheels /var/tmp/svt-wheel-review.dpEiTf/wheels \
        --output /var/tmp/report-amd64-inputs.tar

Repeat with arm64 and a distinct output filename. Output is a
QA artifact, not an executable Docker build context: no Dockerfile
is included and no build or deployment permission is conveyed.
The fixed source scope is retained externally on the development
host; this CLI cannot qualify an arbitrary future source revision.

Eight focused unit tests PASS: exact bytes, changed content,
incorrect length, absent/symlink input, unsafe filename,
deterministic archive, long filename and existing output refusal.
An initial Ruff overlength error was corrected. First real
staging exposed the USTAR filename limit for a wheel; switched
to deterministic PAX with fixed metadata and added the long-name
regression. No partial published archive survived the failure.

Actual full sets staged twice per target, archives byte-identical
within each architecture. Independently inspected 52 regular
members per archive and matched all 51 manifest-listed file
hashes. Missing DEB directory refused without output.
AMD64 archive SHA256:
82a852e1ad112d0fb20b5d74372cb8f097201ff9dc2f4f00faba3ddf831b8ba6
ARM64 archive SHA256:
834d08430354578e9dd8db59748c1743dda1c2c52b601001e1b092299eb3765f
Staging review SHA256:
56d39da62e3e8d0e65087b9f67593e309cb05bcb6976eab3ccad613b416165f5
Protected raw archives:
 /var/tmp/svt-report-build-context.ThYU21

Next: review a separate offline QA build recipe consuming
only the verified platform bundle and exact base identity;
recheck snapshot freshness at use. Actual APT installation,
native runtime, full multiarchitecture build, security assessment
and signing remain open. No release Dockerfile, production,
historical START profile or frozen narrow .29 changes.
Previously blocked integrated trials were not repeated.
