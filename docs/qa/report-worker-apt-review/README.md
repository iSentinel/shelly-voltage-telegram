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
