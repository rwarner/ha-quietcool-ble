"""Tests for the physical fan-speed label helper (Fan Speed sensor + fan attribute)."""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path


COMPONENT = Path(__file__).parents[1] / "custom_components" / "quietcool_ble"
custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(COMPONENT.parent)]
quietcool_ble = types.ModuleType("custom_components.quietcool_ble")
quietcool_ble.__path__ = [str(COMPONENT)]
sys.modules.setdefault("custom_components", custom_components)
sys.modules.setdefault("custom_components.quietcool_ble", quietcool_ble)

bleak = types.ModuleType("bleak")
bleak.BleakClient = object
sys.modules.setdefault("bleak", bleak)

api = importlib.import_module("custom_components.quietcool_ble.api")


def _state(range_value):
    return api.FanState(
        mode="TH",
        range=range_value,
        temp_fahrenheit=80.0,
        humidity_percent=50.0,
    )


class FanSpeedLabelTests(unittest.TestCase):
    def test_none_state_returns_none(self) -> None:
        self.assertIsNone(api.fan_speed_label(None))

    def test_running_speeds(self) -> None:
        self.assertEqual(api.fan_speed_label(_state(api.FanSpeed.HIGH)), "High")
        self.assertEqual(api.fan_speed_label(_state(api.FanSpeed.MEDIUM)), "Medium")
        self.assertEqual(api.fan_speed_label(_state(api.FanSpeed.LOW)), "Low")

    def test_running_speed_as_plain_string(self) -> None:
        # fan_state.range arrives from the device as a plain string.
        self.assertEqual(api.fan_speed_label(_state("HIGH")), "High")

    def test_not_running_is_off(self) -> None:
        # Smart mode active but not currently running: range is None/absent → Off.
        self.assertEqual(api.fan_speed_label(_state(None)), "Off")
        self.assertEqual(api.fan_speed_label(_state("OFF")), "Off")
        self.assertEqual(api.fan_speed_label(_state("whatever")), "Off")


def _state_mode(mode):
    return api.FanState(
        mode=mode, range=None, temp_fahrenheit=80.0, humidity_percent=50.0
    )


class FanModeLabelTests(unittest.TestCase):
    def test_none_state_returns_none(self) -> None:
        self.assertIsNone(api.fan_mode_label(None))

    def test_known_modes(self) -> None:
        self.assertEqual(api.fan_mode_label(_state_mode(api.FanMode.IDLE)), "Idle")
        self.assertEqual(api.fan_mode_label(_state_mode(api.FanMode.TIMER)), "Timer")
        # "TH" (Thermostat+Humidity) displays as the friendlier "Smart".
        self.assertEqual(api.fan_mode_label(_state_mode(api.FanMode.TH)), "Smart")
        self.assertEqual(api.fan_mode_label(_state_mode("TH")), "Smart")

    def test_unknown_mode_passes_through(self) -> None:
        self.assertEqual(api.fan_mode_label(_state_mode("Bootloader")), "Bootloader")


class FanSpeedsForTypeTests(unittest.TestCase):
    """The shared FanType -> speed-list gate used by both the fan presets and
    the Humidity Fan Speed select (#20)."""

    def test_three_speed(self) -> None:
        self.assertEqual(
            api.fan_speeds_for_type("THREE"),
            [api.FanSpeed.LOW, api.FanSpeed.MEDIUM, api.FanSpeed.HIGH],
        )

    def test_single_speed_is_high_only(self) -> None:
        # A single-speed ("ONE") fan exposes only High — no Low, no Medium.
        self.assertEqual(api.fan_speeds_for_type("ONE"), [api.FanSpeed.HIGH])

    def test_two_speed(self) -> None:
        self.assertEqual(
            api.fan_speeds_for_type("TWO"), [api.FanSpeed.LOW, api.FanSpeed.HIGH]
        )

    def test_unknown_and_missing_fall_back_to_low_high(self) -> None:
        # Unknown/garbage values and a missing FanType (None) are never collapsed
        # to one speed; they keep the safe Low/High default.
        for fan_type in ("FOUR", "", "garbage", None):
            self.assertEqual(
                api.fan_speeds_for_type(fan_type),
                [api.FanSpeed.LOW, api.FanSpeed.HIGH],
                msg=f"fan_type={fan_type!r}",
            )

    def test_returns_a_fresh_list(self) -> None:
        # Callers (e.g. the select's `options`) must not be able to mutate shared state.
        first = api.fan_speeds_for_type("ONE")
        first.append(api.FanSpeed.LOW)
        self.assertEqual(api.fan_speeds_for_type("ONE"), [api.FanSpeed.HIGH])


if __name__ == "__main__":
    unittest.main()
