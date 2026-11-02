from pathlib import Path


roadmap = Path("ROADMAP.md")
text = roadmap.read_text(encoding="utf-8")
text = text.replace(
    "- Shelly Digest-auth software path is regression-tested; enable authentication on\n"
    "  the physical Shelly and run the live-device auth check when mains/device access is available.",
    "- Shelly Digest-auth software path is regression-tested. Live auth validation is\n"
    "  deferred until the operator independently enables authentication on the physical\n"
    "  Shelly; this monitor-only project does not change device authentication settings.",
)
text = text.replace(
    "- Signed container releases are being implemented in `0.5.5-hw` with keyless Sigstore Cosign signing of immutable GHCR digests.",
    "- Signed container releases are implemented and independently verified in `0.5.5-hw` using keyless Sigstore Cosign signatures over immutable GHCR digests.",
)
text = text.replace(
    "## `0.5.5-hw` signed container release — SOFTWARE CANDIDATE",
    "## `0.5.5-hw` signed container release — SUPPLY-CHAIN ACCEPTED",
)
text = text.replace(
    "- Keep production on hardware-accepted `0.5.4-hw` while the supply-chain workflow is validated.",
    "- Keep production on hardware-accepted `0.5.4-hw`; `0.5.5-hw` changes the public release supply chain, not runtime monitoring behavior.",
)
text = text.replace(
    "- Final acceptance requires a real GitHub Actions build/sign run and independent `cosign verify` of the published digest.",
    "- Acceptance completed: GitHub Actions run `41373145505` built and keyless-signed RC2 digest `sha256:df534c7b9b30e2a8f3dfc617b07a0ff6837ecc15b285331dc13e211130dcca4d`; independent Gateway `cosign verify` passed with the expected GitHub Actions certificate identity and OIDC issuer.",
)
roadmap.write_text(text, encoding="utf-8")

changelog = Path("CHANGELOG.md")
text = changelog.read_text(encoding="utf-8")
text = text.replace(
    "- Production remains on hardware-accepted `0.5.4-hw` until the signing workflow is proven in GitHub Actions.",
    "- Keyless signing is accepted with GitHub Actions RC2 run `41373145505` plus independent Gateway `cosign verify`; production remains on hardware-accepted `0.5.4-hw` because `0.5.5-hw` changes only the release supply chain.",
)
changelog.write_text(text, encoding="utf-8")
