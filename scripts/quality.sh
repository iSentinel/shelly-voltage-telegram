#!/usr/bin/env bash
# Run the exact Python quality gate used by local checks and GitHub CI.
# Keep this script as the single source of truth for Python quality checks.
set -euo pipefail

REQUIRED_RUFF_VERSION="0.14.2"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

usage() {
    cat <<'HELP'
Usage: scripts/quality.sh [-h|--help]

Run Ruff formatting/lint checks and the project maintainability checker.
Requires Ruff 0.14.2. Set PYTHON_BIN to override the Python executable.
HELP
}

if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "QUALITY GATE: FAIL: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
fi

if ! command -v ruff >/dev/null 2>&1; then
    echo "QUALITY GATE: FAIL: ruff ${REQUIRED_RUFF_VERSION} is required" >&2
    exit 2
fi

actual_version="$(ruff --version | awk '{print $2}')"
if [[ "$actual_version" != "$REQUIRED_RUFF_VERSION" ]]; then
    echo "QUALITY GATE: FAIL: ruff ${REQUIRED_RUFF_VERSION} required; found ${actual_version}" >&2
    exit 2
fi

cd "$ROOT"
ruff format --line-length 79 --check app.py advanced_features.py apartment_phases.py update_info.py telegram_i18n.py notification_features.py dtek_correlation.py dtek_events.py dtek_schedule.py manual_dtek_forecast.py voltage_warning.py outage_data_ua.py yasno_schedule.py yasno_cache.py telegram_channel_yasno.py telegram_widget_data.py telegram_widget_fallback.py scripts tests
ruff check --line-length 79 --select E,F,I,UP,SIM app.py advanced_features.py apartment_phases.py update_info.py telegram_i18n.py notification_features.py dtek_correlation.py dtek_events.py dtek_schedule.py manual_dtek_forecast.py voltage_warning.py outage_data_ua.py yasno_schedule.py yasno_cache.py telegram_channel_yasno.py telegram_widget_data.py telegram_widget_fallback.py scripts tests
"$PYTHON_BIN" scripts/check-quality.py

echo "QUALITY GATE: PASS"
