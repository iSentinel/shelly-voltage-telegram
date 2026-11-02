from pathlib import Path
import re


def replace(path_name, pairs):
    """Apply exact text replacements to one UTF-8 file."""
    path = Path(path_name)
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


Path("LIVE_VERSION").write_text("0.5.4-hw\n", encoding="utf-8")
Path("RELEASE_STATUS.md").write_text(
    """# Release status

## 0.5.4-hw

- Current public release: `0.5.4-hw`.
- Current production live checkpoint: `0.5.4-hw`.
- Hardware acceptance passed on a physical Shelly Pro EM-50.
- CI covers Python 3.11, 3.12 and 3.13.
- The multi-architecture GHCR image and source release are published.
- Public release artifacts exclude deployment `.env`, secrets, SQLite data,
  Telegram identifiers, Shelly credentials and local validation artifacts.

`0.5.4-hw` includes the maintainability audit, permanent PEP 8 and function-size
quality gates, installer upgrade fallback for legacy meter configuration, the
multi-phase data model, native Shelly Pro 3EM / Pro 3EM-3CT63 support, selectable
Shelly backends, charts, CSV export, quality analytics, outage recovery status
rotation, watchdog metrics and the local dashboard.

Physical three-phase acceptance remains pending until a Pro 3EM / Pro 3EM-3CT63
is installed.
""",
    encoding="utf-8",
)

replace(
    "README.md",
    [
        ("- Current release: **0.5.3-hw**", "- Current release: **0.5.4-hw**"),
        (
            "- Production deployment: **0.5.3-hw**, hardware-accepted on a physical Shelly Pro EM-50 after a real outage/recovery cycle.",
            "- Production deployment: **0.5.4-hw**, hardware-accepted on a physical Shelly Pro EM-50 after live upgrade validation.",
        ),
        (
            "- Hardware-accepted live checkpoint: **0.5.3-hw**",
            "- Hardware-accepted live checkpoint: **0.5.4-hw**",
        ),
        (
            "- Candidate `0.5.4-hw` contains the maintainability audit and installer upgrade fixes. Production remains on hardware-accepted `0.5.3-hw` until all candidate gates pass.",
            "- Release `0.5.4-hw` includes the maintainability audit, installer upgrade fixes and permanent quality gates.",
        ),
        (
            "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.3-hw",
            "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.4-hw",
        ),
    ],
)
replace(
    "README_EN.md",
    [
        ("**Current release: `0.5.3-hw`**", "**Current release: `0.5.4-hw`**"),
        (
            "**Production deployment: `0.5.3-hw`**",
            "**Production deployment: `0.5.4-hw`**",
        ),
        (
            "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.3-hw",
            "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.4-hw",
        ),
    ],
)
replace(
    "README_RU.md",
    [
        (
            "Текущий публичный релиз: **`0.5.3-hw`**.",
            "Текущий публичный релиз: **`0.5.4-hw`**.",
        ),
        (
            "Рабочий production уже использует **`0.5.3-hw`**",
            "Рабочий production уже использует **`0.5.4-hw`**",
        ),
        (
            "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.3-hw",
            "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.4-hw",
        ),
    ],
)
replace(
    "README_UA.md",
    [
        (
            "Поточний публічний реліз: **`0.5.3-hw`**.",
            "Поточний публічний реліз: **`0.5.4-hw`**.",
        ),
        (
            "Робочий production уже використовує **`0.5.3-hw`**",
            "Робочий production уже використовує **`0.5.4-hw`**",
        ),
        (
            "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.3-hw",
            "ghcr.io/isentinel/shelly-voltage-telegram:v0.5.4-hw",
        ),
    ],
)

patterns = {
    "README_EN.md": (
        r"- Public validation metadata in this release still carries the earlier line: .*?next audited release\.",
        "- **Hardware-accepted live checkpoint: `0.5.4-hw`**.",
    ),
    "README_RU.md": (
        r"- В validation metadata этого релиза .*?следующем аудированном релизе\.",
        "- Аппаратно подтверждённый live checkpoint: **`0.5.4-hw`**.",
    ),
    "README_UA.md": (
        r"- У validation metadata цього релізу .*?наступному аудованому релізі\.",
        "- Апаратно підтверджений live checkpoint: **`0.5.4-hw`**.",
    ),
}
for name, (pattern, value) in patterns.items():
    path = Path(name)
    text = re.sub(
        pattern,
        value,
        path.read_text(encoding="utf-8"),
        count=1,
    )
    path.write_text(text, encoding="utf-8")

hardware = Path("HARDWARE_VALIDATION.md")
text = hardware.read_text(encoding="utf-8")
text = re.sub(
    r"^Date: .*?$",
    "Date: 2026-11-02",
    text,
    count=1,
    flags=re.M,
)
text = re.sub(
    r"^Checkpoint: `[^`]+`$",
    "Checkpoint: `0.5.4-hw`",
    text,
    count=1,
    flags=re.M,
)
text = text.replace(
    "## 2026-11-02 `0.5.4-hw` audit smoke\n\n"
    "The maintainability candidate was validated in isolation while production stayed\n"
    "online on `0.5.3-hw`.",
    "## 2026-11-02 `0.5.4-hw` audit and promotion acceptance\n\n"
    "The maintainability candidate was first validated in isolation and then promoted\n"
    "to production after the full software, package and hardware gates passed.",
)
if "Production acceptance after promotion:" not in text:
    text += """

Production acceptance after promotion:

- production version and `LIVE_VERSION`: `0.5.4-hw`;
- container health: healthy;
- `/ready`: HTTP 200 / online;
- physical Shelly Pro EM-50: online during the post-promotion check;
- SQLite schema metadata: 3;
- SQLite `PRAGMA quick_check`: `ok`;
- open outage records: 0;
- synthetic `0 V` rows: 0;
- persisted Telegram status message retained across promotion;
- recent runtime logs contained no application error or warning entries;
- deployed `app.py` SHA256 matches the audited `0.5.4-hw` candidate.
"""
hardware.write_text(text, encoding="utf-8")

roadmap = Path("ROADMAP.md")
text = roadmap.read_text(encoding="utf-8")
text = re.sub(
    r"^Status date: .*?$",
    "Status date: 2026-11-02",
    text,
    count=1,
    flags=re.M,
)
text = re.sub(
    r"^Live checkpoint: .*?$",
    "Live checkpoint: `0.5.4-hw` (hardware accepted on CT110)",
    text,
    count=1,
    flags=re.M,
)
text = re.sub(
    r"^Previous rollback checkpoint: .*?$",
    "Previous hardware checkpoint: `0.5.3-hw`",
    text,
    count=1,
    flags=re.M,
)
publication = """### 11. GitHub publication — DONE

- Public repository: `iSentinel/shelly-voltage-telegram`.
- Multilingual README pages and the live dashboard image are published.
- Release `v0.5.4-hw` is published with source archive and `SHA256SUMS`.
- CI, container image and source-release workflows are active.
- Public release excludes `.env`, secrets, SQLite and local validation data.
- The one-line installer points to the real public repository.
- Public bootstrap dry-run was validated on a clean Debian 13 container.
"""
text = re.sub(
    r"### 11\. GitHub publication .*?(?=\n## Optional hardening after acceptance)",
    publication.rstrip(),
    text,
    count=1,
    flags=re.S,
)
roadmap.write_text(text, encoding="utf-8")
