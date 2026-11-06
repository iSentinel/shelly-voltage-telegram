"""Deterministic tests for optional early low-voltage advisory logic."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace

from voltage_warning import (
    WarningPolicy,
    WarningState,
    observe_warning,
)

POLICY = WarningPolicy(enabled=True)


def samples(
    voltage: float,
    start: int = 1000,
    count: int = 20,
    interval: int = 10,
    policy: WarningPolicy = POLICY,
) -> tuple[WarningState, list[str]]:
    """Apply regular observations without live hardware or telemetry."""

    state = WarningState()
    events = []
    for index in range(count):
        state, event = observe_warning(
            state, voltage, start + interval * index, policy
        )
        if event:
            events.append(event)
    return state, events


class VoltageWarningTests(unittest.TestCase):
    """Keep advisory warning states separate from critical power monitoring."""

    def test_disabled_by_default(self):
        state, events = samples(190.0, policy=WarningPolicy())
        self.assertFalse(state.active)
        self.assertEqual(events, [])

    def test_low_voltage_dwell_required(self):
        state, events = samples(205.2, count=18)
        self.assertFalse(state.active)
        self.assertEqual(events, [])

    def test_sustained_below_207_starts_one_warning(self):
        state, events = samples(205.2, count=23)
        self.assertTrue(state.active)
        self.assertEqual(events, ["warning_started"])

    def test_exact_207_does_not_trigger(self):
        state, events = samples(207.0, count=23)
        self.assertFalse(state.active)
        self.assertEqual(events, [])

    def test_sustained_180_critical_and_warning_are_separate(self):
        state, events = samples(179.0, count=23)
        self.assertTrue(state.active)
        self.assertEqual(events, ["warning_started"])

    def test_outage_placeholder_zero_is_invalid(self):
        state, events = samples(0.0, count=23)
        self.assertFalse(state.active)
        self.assertEqual(events, [])

    def test_none_does_not_start_warning(self):
        state, events = samples(None, count=23)
        self.assertFalse(state.active)
        self.assertEqual(events, [])

    def test_nonfinite_voltage_does_not_trigger(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                state, events = samples(value, count=23)
                self.assertFalse(state.active)
                self.assertEqual(events, [])

    def test_older_timestamp_ignored(self):
        state, event = observe_warning(
            WarningState(last_ts=1000), 190, 999, POLICY
        )
        self.assertIsNone(event)
        self.assertEqual(state.last_ts, 1000)

    def test_duplicate_timestamp_ignored(self):
        state, event = observe_warning(
            WarningState(last_ts=1000), 190, 1000, POLICY
        )
        self.assertIsNone(event)
        self.assertEqual(state.last_ts, 1000)

    def test_network_gap_resets_pending_dwell(self):
        state, events = samples(205, count=12)
        self.assertEqual(events, [])
        state, event = observe_warning(state, 205, 1300, POLICY)
        self.assertIsNone(event)
        self.assertFalse(state.active)
        self.assertEqual(state.low_since, 1300)

    def test_recovery_requires_210_for_two_minutes(self):
        state, events = samples(205, count=20)
        self.assertTrue(state.active)
        for now in range(1200, 1320, 10):
            state, event = observe_warning(state, 211, now, POLICY)
            self.assertIsNone(event)
        state, event = observe_warning(state, 211, 1320, POLICY)
        self.assertEqual(event, "warning_cleared")
        self.assertFalse(state.active)

    def test_hysteresis_below_210_keeps_warning(self):
        state, events = samples(205, count=20)
        self.assertTrue(state.active)
        for now in range(1200, 1410, 10):
            state, event = observe_warning(state, 209.9, now, POLICY)
            self.assertIsNone(event)
        self.assertTrue(state.active)

    def test_transient_recovery_resets_clear_dwell(self):
        state, events = samples(205, count=20)
        for now in (1200, 1210, 1220, 1230):
            state, _ = observe_warning(state, 211, now, POLICY)
        state, _ = observe_warning(state, 205, 1240, POLICY)
        state, event = observe_warning(state, 211, 1250, POLICY)
        self.assertIsNone(event)
        self.assertEqual(state.clear_since, 1250)

    def test_offline_sample_never_clears_warning(self):
        state, events = samples(205, count=20)
        state, event = observe_warning(state, None, 1200, POLICY)
        self.assertIsNone(event)
        self.assertTrue(state.active)
        self.assertIsNone(state.clear_since)

    def test_gap_discards_pending_recovery(self):
        state, events = samples(205, count=20)
        state, _ = observe_warning(state, 211, 1200, POLICY)
        state, event = observe_warning(state, 211, 1350, POLICY)
        self.assertTrue(state.active)
        self.assertIsNone(event)
        self.assertEqual(state.clear_since, 1350)

    def test_no_duplicate_start_on_continued_low_samples(self):
        state, events = samples(205, count=25)
        self.assertEqual(events.count("warning_started"), 1)
        for now in range(1250, 1300, 10):
            state, event = observe_warning(state, 205, now, POLICY)
            self.assertIsNone(event)
        self.assertTrue(state.active)

    def test_policy_must_have_hysteresis(self):
        with self.assertRaisesRegex(ValueError, "policy"):
            observe_warning(
                WarningState(),
                200,
                1000,
                replace(POLICY, clear_v=206),
            )

    def test_policy_rejects_instant_trigger(self):
        with self.assertRaisesRegex(ValueError, "policy"):
            observe_warning(
                WarningState(),
                200,
                1000,
                replace(POLICY, dwell_sec=0),
            )

    def test_readings_up_to_500_are_bounded(self):
        state, events = samples(1000.0, count=23)
        self.assertFalse(state.active)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
