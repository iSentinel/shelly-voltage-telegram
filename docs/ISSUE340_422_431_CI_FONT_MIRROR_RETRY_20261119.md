# Issues 340/422/431: bounded CI font-mirror retry

Date: 2026-11-19. Scope: GitHub CI reliability only.

GitHub Actions run 44520393027 on source SHA
f8b7aae840354f22759d547c11bddad3ace34673 failed before
Python unit tests on the Python3.12 runner. The "Install v5 font
packages" apt step could not retrieve fonts-inter and fonts-noto-core
from Ubuntu 26.04 Azure/security mirrors; network failures and
timeouts were observed. Python3.13 completed successfully, and the
other matrix job was cancelled. This was an external dependency
failure and is not evidence of a Python regression PASS.

The test workflow now retries the apt update+installation as one
unit up to three times, with per-request timeouts and bounded
Acquire::Retries for transient network problems. It then explicitly
requires dpkg to report both fonts installed. The job still FAILS
if the fonts are unavailable after retries; the graphic rendering
test dependencies are not optional and there is no skip/bypass.

The source-only CI policy tests, exact-SHA build/signing workflow
tests and yaml parse pass: 31/31 focused tests. Project Ruff0.14.2
and maintainability gate pass.

No production services, source artifacts, GitHub main, tags or
images were touched. The next independent complete GitHub CI
matrix must validate this change on its own exact SHA.
