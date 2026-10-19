"""Regression tests for QuietCool Bluetooth discovery variants.

Covers both the named ``ATTICFAN`` controllers and the name-less revisions that
advertise only the ``tticfan`` manufacturer-data signature (company ID 0x6133).
"""
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

discovery = importlib.import_module("custom_components.quietcool_ble.discovery")


def service_info(
    *, name: str | None, address: str, manufacturer_data: dict[int, bytes]
) -> types.SimpleNamespace:
    """Build the BluetoothServiceInfo surface used by discovery."""
    return types.SimpleNamespace(
        name=name, address=address, manufacturer_data=manufacturer_data
    )


class DiscoveryTests(unittest.TestCase):
    def test_normal_named_controller(self) -> None:
        info = service_info(
            name="ATTICFAN_1234", address="AA:BB:CC:DD:EE:FF", manufacturer_data={}
        )
        self.assertTrue(discovery.is_quietcool_advertisement(info))
        self.assertEqual(discovery.quietcool_display_name(info), "ATTICFAN_1234")

    def test_solar_named_controller_rejected(self) -> None:
        # Solar models advertise SOLARFAN-* but run Bluetooth Mesh, not this
        # vendor protocol, so they are intentionally not discovered (#19).
        info = service_info(
            name="SOLARFAN-787de106b2",
            address="50:78:7D:E1:06:B2",
            manufacturer_data={},
        )
        self.assertFalse(discovery.is_quietcool_advertisement(info))

    def test_unrelated_named_device_rejected(self) -> None:
        info = service_info(
            name="MyFitnessBand", address="12:34:56:78:9A:BC", manufacturer_data={}
        )
        self.assertFalse(discovery.is_quietcool_advertisement(info))

    def test_mac_only_manufacturer_variant(self) -> None:
        info = service_info(
            name="11:22:33:44:55:66",
            address="11:22:33:44:55:66",
            manufacturer_data={0x6133: b"tticfan" + bytes(17)},
        )
        self.assertTrue(discovery.is_quietcool_advertisement(info))
        self.assertEqual(
            discovery.quietcool_display_name(info),
            "QuietCool Fan (11:22:33:44:55:66)",
        )

    def test_lookalike_manufacturer_payload_is_rejected(self) -> None:
        info = service_info(
            name="11:22:33:44:55:66",
            address="11:22:33:44:55:66",
            manufacturer_data={0x6133: b"not-a-fan"},
        )
        self.assertFalse(discovery.is_quietcool_advertisement(info))

    def test_unrelated_device_is_rejected(self) -> None:
        info = service_info(
            name="Some Other Device",
            address="99:88:77:66:55:44",
            manufacturer_data={0x004C: b"\x02\x15rest-of-ibeacon"},
        )
        self.assertFalse(discovery.is_quietcool_advertisement(info))

    def test_advertisement_fallback_surface(self) -> None:
        # Some discovery objects expose manufacturer_data only via .advertisement.
        info = types.SimpleNamespace(
            name="AA:BB:CC:DD:EE:FF",
            address="AA:BB:CC:DD:EE:FF",
            manufacturer_data=None,
            advertisement=types.SimpleNamespace(
                manufacturer_data={0x6133: b"tticfan"}
            ),
        )
        self.assertTrue(discovery.is_quietcool_advertisement(info))


if __name__ == "__main__":
    unittest.main()
