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
