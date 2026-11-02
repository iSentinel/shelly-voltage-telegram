from pathlib import Path


def replace_once(path_name, old, new):
    """Replace one required marker in a UTF-8 text file."""
    path = Path(path_name)
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing marker in {path_name}: {old[:60]}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


Path("VERSION").write_text("0.5.5-hw\n", encoding="utf-8")

replace_once(
    "README.md",
    "## Status\n\n",
    "## Status\n\n- Current software candidate: **0.5.5-hw**\n",
)
replace_once(
    "README.md",
    "- Hardware-accepted live checkpoint: **0.5.4-hw**\n",
    "- Hardware-accepted live checkpoint: **0.5.4-hw**\n"
    "- `0.5.5-hw` adds keyless Sigstore signing for versioned GHCR images; "
    "production remains on `0.5.4-hw` until release gates pass.\n",
)
replace_once(
    "README_EN.md",
    "- **Current release: `0.5.4-hw`**\n",
    "- **Current software candidate: `0.5.5-hw`**\n"
    "- **Current release: `0.5.4-hw`**\n",
)
replace_once(
    "README_EN.md",
    "The release image is multi-architecture (`linux/amd64`, `linux/arm64`) and is published with SBOM/provenance metadata.\n",
    "The release image is multi-architecture (`linux/amd64`, `linux/arm64`) "
    "and is published with SBOM/provenance metadata. Candidate `0.5.5-hw` "
    "also signs versioned GHCR images keylessly with Sigstore Cosign.\n",
)
replace_once(
    "README_RU.md",
    "- Текущий публичный релиз: **`0.5.4-hw`**.\n",
    "- Текущий software candidate: **`0.5.5-hw`**.\n"
    "- Текущий публичный релиз: **`0.5.4-hw`**.\n",
)
replace_once(
    "README_RU.md",
    "Образ выпускается для `linux/amd64` и `linux/arm64` с SBOM и provenance metadata.\n",
    "Образ выпускается для `linux/amd64` и `linux/arm64` с SBOM и provenance "
    "metadata. В candidate `0.5.5-hw` versioned GHCR-образы также "
    "подписываются keyless-подписью Sigstore Cosign.\n",
)
replace_once(
    "README_UA.md",
    "- Поточний публічний реліз: **`0.5.4-hw`**.\n",
    "- Поточний software candidate: **`0.5.5-hw`**.\n"
    "- Поточний публічний реліз: **`0.5.4-hw`**.\n",
)
replace_once(
    "README_UA.md",
    "Образ випускається для `linux/amd64` і `linux/arm64` із SBOM та provenance metadata.\n",
    "Образ випускається для `linux/amd64` і `linux/arm64` із SBOM та provenance "
    "metadata. У candidate `0.5.5-hw` versioned GHCR-образи також "
    "підписуються keyless-підписом Sigstore Cosign.\n",
)

path = Path("CHANGELOG.md")
text = path.read_text(encoding="utf-8")
entry = """## 0.5.5-hw - supply-chain signing candidate

- Add keyless Sigstore Cosign signing for versioned GHCR images.
- Sign the immutable multi-architecture image digest after a successful push.
- Keep ordinary `main` images unsigned so release signatures remain easy to interpret.
- Pin the official `sigstore/cosign-installer` action to a full commit SHA.
- Add regression coverage for signing permissions, digest use and release-only conditions.
- Production remains on hardware-accepted `0.5.4-hw` until the signing workflow is proven in GitHub Actions.

"""
insert_at = text.find("\n## ")
if insert_at == -1:
    text += "\n" + entry
else:
    text = text[: insert_at + 1] + entry + text[insert_at + 1 :]
path.write_text(text, encoding="utf-8")

path = Path("ROADMAP.md")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "### 3. Telegram implementation — SOFTWARE DONE / LIVE CREDENTIAL TEST PENDING",
    "### 3. Telegram implementation — DONE / LIVE ACCEPTED",
)
text = text.replace(
    "Pending only external credentials and live Telegram acceptance.",
    "Live Telegram acceptance is complete; current bot and status-channel "
    "destinations are validated by `doctor` and the live command/status flow.",
)
text = text.replace(
    "### 8. Installation UX — DONE FOR LOCAL / GITHUB URL PENDING",
    "### 8. Installation UX — DONE / PUBLIC BOOTSTRAP ACCEPTED",
)
text = text.replace(
    "- future `curl | bash` bootstrap;",
    "- public `curl | bash` bootstrap, including clean Debian 13 dry-run validation;",
)
text = text.replace(
    "## `0.5.4-hw` maintainability audit — SOFTWARE/HARDWARE SMOKE PASS",
    "## `0.5.4-hw` maintainability audit — RELEASED / HARDWARE ACCEPTED",
)
text = text.replace(
    "- Keep production on hardware-accepted `0.5.3-hw` until the audited release is\n"
    "  merged and promoted through the normal installer path.\n",
    "- Production and public release are both `0.5.4-hw`; live upgrade acceptance passed.\n",
)
text = text.replace(
    "- Consider signed container releases after GitHub publication.",
    "- Signed container releases are being implemented in `0.5.5-hw` with "
    "keyless Sigstore Cosign signing of immutable GHCR digests.",
)
text += """

## `0.5.5-hw` signed container release — SOFTWARE CANDIDATE

- Keep production on hardware-accepted `0.5.4-hw` while the supply-chain workflow is validated.
- Versioned GHCR images install Cosign through an official action pinned to a full commit SHA.
- Release images are signed keylessly through GitHub OIDC; no long-lived signing key is stored in repository secrets.
- The signature targets the immutable image digest returned by the multi-architecture build.
- Ordinary `main` images remain unsigned; only versioned release images receive the release signature.
- Regression tests enforce OIDC permission, pinned action SHA, digest-based signing and release-only conditions.
- Final acceptance requires a real GitHub Actions build/sign run and independent `cosign verify` of the published digest.
"""
path.write_text(text, encoding="utf-8")

path = Path("tests/test_workflows.py")
text = path.read_text(encoding="utf-8")
marker = "    def test_release_uses_exact_tag_and_checksum(self):\n"
test = '''    def test_release_images_are_keyless_signed_by_digest(self):
        """Versioned GHCR images must be signed with Sigstore Cosign."""
        text = (WORKFLOWS / "image.yml").read_text(encoding="utf-8")
        self.assertIn("id-token: write", text)
        self.assertIn(
            "sigstore/cosign-installer@"
            "6f9f17788090df1f26f669e9d70d6ae9567deba6",
            text,
        )
        self.assertIn("id: build", text)
        self.assertIn(
            "IMAGE_DIGEST: ${{ steps.build.outputs.digest }}", text
        )
        self.assertIn(
            'cosign sign --yes "${IMAGE_NAME}@${IMAGE_DIGEST}"', text
        )
        self.assertIn("startsWith(inputs.image_tag, 'v')", text)

'''
if marker not in text:
    raise SystemExit("workflow test marker not found")
path.write_text(text.replace(marker, test + marker, 1), encoding="utf-8")
