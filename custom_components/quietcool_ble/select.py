"""QuietCool BLE select entities.

  Mode (operating mode):
    Idle  — fan off
    Timer — fan runs for a set duration (uses stored parameter defaults)
    TH    — Thermostat+Humidity smart mode; auto-runs based on temp/humidity thresholds

  Humidity Fan Speed (device GetHum_Range / app "Desired Speed"):
    The speed the fan uses when *humidity* (the Turn Fan On threshold) is what turns it
    on. Written via SetTempHumidity alongside the other smart-mode params.
"""
from __future__ import annotations

import dataclasses
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import api
from .api import FanMode, FanSpeed
from .const import DOMAIN
from .coordinator import QuietCoolBLECoordinator

_LOGGER = logging.getLogger(__name__)

# All known operating modes.  The device may return others; we pass them through.
FAN_MODES = [FanMode.IDLE, FanMode.TIMER, FanMode.TH]

# Speeds selectable for humidity-driven runs. 2-speed fans ignore MEDIUM.
HUM_SPEEDS = [FanSpeed.LOW, FanSpeed.MEDIUM, FanSpeed.HIGH]


async def _maybe_reassert_th(coordinator: QuietCoolBLECoordinator) -> None:
    """Re-assert TH if the fan is already in TH so a parameter change takes effect."""
    state = coordinator.fan_state
    if state is not None and state.mode == FanMode.TH:
        protocol = coordinator.fan_info.protocol
        await coordinator.async_execute(lambda client: api.set_mode_th(client, protocol=protocol))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: QuietCoolBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            QuietCoolModeSelectEntity(coordinator),
            QuietCoolHumSpeedSelectEntity(coordinator),
        ]
    )


class _QuietCoolSelectBase(CoordinatorEntity[QuietCoolBLECoordinator], SelectEntity):
    """Shared device_info for QuietCool select entities."""

    _attr_has_entity_name = True

    @property
    def device_info(self) -> DeviceInfo:
        version = self.coordinator.fan_version
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=self.coordinator.fan_info.name,
            manufacturer="QuietCool",
            model=self.coordinator.fan_info.model or None,
            sw_version=version.firmware if version else None,
            hw_version=version.hw_version if version else None,
        )


class QuietCoolModeSelectEntity(_QuietCoolSelectBase):
    """Select entity for choosing the fan's operating mode."""

    _attr_name = "Mode"
    _attr_options = FAN_MODES

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_mode"

    @property
    def available(self) -> bool:
        return self.coordinator.fan_state is not None

    @property
    def current_option(self) -> str | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.mode

    async def async_select_option(self, option: str) -> None:
        protocol = self.coordinator.fan_info.protocol

        if option == FanMode.IDLE:
            await self.coordinator.async_execute(
                lambda client: api.set_mode_idle(client, protocol=protocol)
            )
        elif option == FanMode.TH:
            await self.coordinator.async_execute(
                lambda client: api.set_mode_th(client, protocol=protocol)
            )
        elif option == FanMode.TIMER:
            # Use device's stored timer defaults; fall back to 8h LOW if not yet fetched
            params = self.coordinator.fan_parameters
            hours, minutes = api.resolve_timer_duration(params)
            speed = params.timer_range if params is not None else FanSpeed.LOW
            await self.coordinator.async_execute(
                lambda client: api.set_mode_timer(
                    client, speed, hours, minutes, protocol=protocol
                )
            )
        else:
            _LOGGER.warning("QuietCool: unknown mode selected: %r", option)
            return

        # Optimistic update so UI reflects the change immediately
        if self.coordinator.fan_state is not None:
            self.coordinator.fan_state = dataclasses.replace(
                self.coordinator.fan_state, mode=option
            )
        self.async_write_ha_state()


class QuietCoolHumSpeedSelectEntity(_QuietCoolSelectBase):
    """Fan speed used when humidity turns the fan on (device GetHum_Range).

    Written via SetTempHumidity (passing the other five params through unchanged),
    then TH is re-asserted so the change takes effect immediately — but only when the
    fan is already in TH, so we never yank it out of Idle/Timer.
    """

    _attr_name = "Humidity Fan Speed"
    _attr_options = HUM_SPEEDS
    _attr_icon = "mdi:fan-speed-1"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_hum_range"

    @property
    def available(self) -> bool:
        return self.coordinator.fan_parameters is not None

    @property
    def current_option(self) -> str | None:
        params = self.coordinator.fan_parameters
        if params is None:
            return None
        return params.hum_range if params.hum_range in HUM_SPEEDS else None

    async def async_select_option(self, option: str) -> None:
        params = self.coordinator.fan_parameters
        if params is None:
            return
        if option not in HUM_SPEEDS:
            _LOGGER.warning("QuietCool: unknown humidity speed: %r", option)
            return
        protocol = self.coordinator.fan_info.protocol
        merged = {
            "temp_h": params.temp_h,
            "temp_m": params.temp_m,
            "temp_l": params.temp_l,
            "hum_h": params.hum_h,
            "hum_l": params.hum_l,
            "hum_range": option,
        }
        await self.coordinator.async_execute(
            lambda client: api.set_temp_humidity(client, protocol=protocol, **merged)
        )
        await _maybe_reassert_th(self.coordinator)
        self.coordinator.fan_parameters = dataclasses.replace(params, hum_range=option)
        self.async_write_ha_state()
