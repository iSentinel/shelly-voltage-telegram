from pathlib import Path


root = Path(".")

p = root / "README.md"
text = p.read_text(encoding="utf-8")
start = text.index("## Status\n")
end = text.index("\n## Supported devices", start)
status = """## Status

- Current release: **0.5.5-hw**
- Production deployment: **0.5.4-hw**, hardware-accepted on a physical Shelly Pro EM-50 after live upgrade validation.
- Hardware-accepted live checkpoint: **0.5.4-hw**
- Release `0.5.5-hw` adds keyless Sigstore signing for versioned GHCR images; runtime monitoring behavior remains the hardware-accepted `0.5.4-hw` code path.
- Shelly Pro 3EM / Pro 3EM-3CT63 support is implemented and software-tested; physical three-phase validation follows after device installation.
"""
text = text[:start] + status + text[end:]
text = text.replace(
    "docker pull ghcr.io/isentinel/shelly-voltage-telegram:v0.5.4-hw",
    "docker pull ghcr.io/isentinel/shelly-voltage-telegram:v0.5.5-hw",
)
p.write_text(text, encoding="utf-8")

for name, replacements in {
    "README_EN.md": [
        ("- **Current software candidate: `0.5.5-hw`**\n", ""),
        ("- **Current release: `0.5.4-hw`**", "- **Current release: `0.5.5-hw`**"),
        ("ghcr.io/isentinel/shelly-voltage-telegram:v0.5.4-hw", "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.5-hw"),
        ("Candidate `0.5.5-hw` also signs versioned GHCR images keylessly with Sigstore Cosign.", "Release `0.5.5-hw` signs versioned GHCR images keylessly with Sigstore Cosign."),
    ],
    "README_RU.md": [
        ("- Текущий software candidate: **`0.5.5-hw`**.\n", ""),
        ("- Текущий публичный релиз: **`0.5.4-hw`**.", "- Текущий публичный релиз: **`0.5.5-hw`**."),
        ("ghcr.io/isentinel/shelly-voltage-telegram:v0.5.4-hw", "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.5-hw"),
        ("В candidate `0.5.5-hw`", "В релизе `0.5.5-hw`"),
    ],
    "README_UA.md": [
        ("- Поточний software candidate: **`0.5.5-hw`**.\n", ""),
        ("- Поточний публічний реліз: **`0.5.4-hw`**.", "- Поточний публічний реліз: **`0.5.5-hw`**."),
        ("ghcr.io/isentinel/shelly-voltage-telegram:v0.5.4-hw", "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.5-hw"),
        ("У candidate `0.5.5-hw`", "У релізі `0.5.5-hw`"),
    ],
}.items():
    p = root / name
    text = p.read_text(encoding="utf-8")
    for old, new in replacements:
        text = text.replace(old, new)
    p.write_text(text, encoding="utf-8")

p = root / "tests/test_metadata.py"
text = p.read_text(encoding="utf-8")
old = '''        if version == live_version:
            self.assertIn(f"Current release: **{version}**", readme)
        else:
            self.assertIn(f"Current software candidate: **{version}**", readme)
'''
new = '''        self.assertIn(f"Current release: **{version}**", readme)
'''
if old not in text:
    raise SystemExit("metadata semantics marker not found")
p.write_text(text.replace(old, new, 1), encoding="utf-8")

(root / "RELEASE_STATUS.md").write_text('''# Release status

## 0.5.5-hw

- Current public release: `0.5.5-hw`.
- Current production live checkpoint remains hardware-accepted `0.5.4-hw`.
- Runtime monitoring behavior is unchanged from the accepted 0.5.4 deployment.
- GitHub Source Release run `41373981210` published `v0.5.5-hw` from exact
  source SHA `f669dbb628854ea6bdbedd61d3b505fe613c93bf`.
- Source assets include `shelly-voltage-telegram-0.5.5-hw.tar.gz` and
  `SHA256SUMS`.
- GHCR run `41373982691` published the amd64/arm64 image with SBOM,
  provenance and keyless Sigstore Cosign signing.
- Final image digest:
  `sha256:c2115def9975dcfbb4a722d08713450403f53beaa01e6c54fec75962d82ce47a`.
- Independent Gateway `cosign verify` passed with the expected GitHub Actions
  workflow certificate identity and OIDC issuer.
- Production does not need an application upgrade for this supply-chain-only
  release.

Physical three-phase acceptance remains pending until a Pro 3EM / Pro 3EM-3CT63
is installed. Physical Digest-auth validation remains operator-controlled; the
monitor does not change Shelly authentication settings.
''', encoding="utf-8")

p = root / "ROADMAP.md"
text = p.read_text(encoding="utf-8")
text = text.replace(
    "## `0.5.5-hw` signed container release — SUPPLY-CHAIN ACCEPTED",
    "## `0.5.5-hw` signed container release — RELEASED / SUPPLY-CHAIN ACCEPTED",
)
if "Final `v0.5.5-hw` publication completed" not in text:
    marker = "- Acceptance completed: GitHub Actions run `41373145505` built and keyless-signed RC2 digest `sha256:df534c7b9b30e2a8f3dfc617b07a0ff6837ecc15b285331dc13e211130dcca4d`; independent Gateway `cosign verify` passed with the expected GitHub Actions certificate identity and OIDC issuer.\n"
    extra = "- Final `v0.5.5-hw` publication completed: Source Release run `41373981210` and signed Container Image run `41373982691` succeeded from exact tag source `f669dbb628854ea6bdbedd61d3b505fe613c93bf`; final image digest is `sha256:c2115def9975dcfbb4a722d08713450403f53beaa01e6c54fec75962d82ce47a`, independently verified through Gateway.\n"
    text = text.replace(marker, marker + extra, 1)
p.write_text(text, encoding="utf-8")

p = root / "CHANGELOG.md"
text = p.read_text(encoding="utf-8")
if "Final `v0.5.5-hw` release published" not in text:
    marker = "- Keyless signing is accepted with GitHub Actions RC2 run `41373145505` plus independent Gateway `cosign verify`; production remains on hardware-accepted `0.5.4-hw` because `0.5.5-hw` changes only the release supply chain.\n"
    extra = "- Final `v0.5.5-hw` release published from exact source SHA `f669dbb628854ea6bdbedd61d3b505fe613c93bf`; source assets and the signed amd64/arm64 GHCR image passed final release verification.\n"
    text = text.replace(marker, marker + extra, 1)
p.write_text(text, encoding="utf-8")
