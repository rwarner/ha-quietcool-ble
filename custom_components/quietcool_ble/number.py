"""QuietCool BLE number entities — smart-mode thresholds and timer duration.

These thresholds control TH (Thermostat+Humidity) smart mode. Per the QuietCool
Smart Control app's own help text (authoritative — the wire field names are the
inverse of what they sound like):
  GetTemp_H  — fan activates (HIGH speed on 2-speed fans) above this
  GetTemp_M  — fan switches from LOW to HIGH speed above this (2-speed fans)
  GetTemp_L  — fan deactivates below this
  GetHum_H   — "Turn Fan Off": fan turns OFF at/above this humidity  (factory 90)
  GetHum_L   — "Turn Fan On":  fan turns ON above this humidity, regardless of
               temperature (factory 70). 255 = feature OFF/disabled.
  GetHum_Range — fan speed used when humidity is what turns the fan on
               (exposed as the "Humidity Fan Speed" select).

Smart-mode check order (from the app help): Turn Fan Off (hum_h) is checked FIRST as
a hard cutout, then temperature, then Turn Fan On (hum_l).

All threshold values are read from GetParameter and written via SetTempHumidity.
SetTempHumidity requires all six fields (H/M/L for temp, H/L/Range for hum);
unchanged fields are passed through from the current coordinator.fan_parameters.

Timer duration (GetHour / GetMinute):
  The duration a Timer-mode activation counts down from. Read from GetParameter
  and written via SetTime alone (no SetMode), so setting it does not start the
  fan — it just changes the stored default the next turn-on / Timer mode uses.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import api
from .const import DOMAIN
from .coordinator import QuietCoolBLECoordinator

_LOGGER = logging.getLogger(__name__)

# 255 is the device sentinel meaning "humidity turn-on feature is OFF".
HUM_L_DISABLED = 255


async def _maybe_reassert_th(coordinator: QuietCoolBLECoordinator) -> None:
    """Re-assert TH if the fan is already in TH so a parameter change takes effect.

    The fan only re-runs its smart-mode decision on a SetMode command.
    """
    state = coordinator.fan_state
    if state is not None and state.mode == api.FanMode.TH:
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
            QuietCoolTempHighNumber(coordinator),
            QuietCoolTempMedNumber(coordinator),
            QuietCoolTempLowNumber(coordinator),
            QuietCoolHumHighNumber(coordinator),
            QuietCoolHumLowNumber(coordinator),
            QuietCoolTimerHoursNumber(coordinator),
            QuietCoolTimerMinutesNumber(coordinator),
        ]
    )


class _QuietCoolThresholdBase(
    CoordinatorEntity[QuietCoolBLECoordinator], NumberEntity
):
    """Base class for smart-mode threshold number entities."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: QuietCoolBLECoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"

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

    @property
    def available(self) -> bool:
        return self.coordinator.fan_parameters is not None

    def _write_thresholds(self, **overrides: Any) -> Any:
        """Build a coroutine that calls SetTempHumidity with all six fields.

        Callers pass only the field(s) they are changing; the rest come from
        the current fan_parameters so unchanged values are preserved.
        """
        params = self.coordinator.fan_parameters
        assert params is not None
        protocol = self.coordinator.fan_info.protocol
        merged = {
            "temp_h": params.temp_h,
            "temp_m": params.temp_m,
            "temp_l": params.temp_l,
            "hum_h": params.hum_h,
            "hum_l": params.hum_l,
            "hum_range": params.hum_range,
        }
        merged.update(overrides)
        return lambda client: api.set_temp_humidity(client, protocol=protocol, **merged)

    async def _apply(self, **overrides: Any) -> None:
        """Write the changed field(s), then re-assert TH so the change takes effect."""
        params = self.coordinator.fan_parameters
        if params is None:
            return
        await self.coordinator.async_execute(self._write_thresholds(**overrides))
        await _maybe_reassert_th(self.coordinator)
        self.coordinator.fan_parameters = dataclasses.replace(params, **overrides)
        self.async_write_ha_state()


class QuietCoolTempHighNumber(_QuietCoolThresholdBase):
    """High temperature threshold for TH smart mode (fan turns on above this)."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = 50
    _attr_native_max_value = 120
    _attr_native_step = 1
    _attr_name = "High Temp Threshold"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "temp_h")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.temp_h)

    async def async_set_native_value(self, value: float) -> None:
        await self._apply(temp_h=int(value))


class QuietCoolTempMedNumber(_QuietCoolThresholdBase):
    """Medium temperature threshold for TH smart mode (2-speed: LOW→HIGH above this)."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = 50
    _attr_native_max_value = 110
    _attr_native_step = 1
    _attr_name = "Medium Temp Threshold"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "temp_m")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.temp_m)

    async def async_set_native_value(self, value: float) -> None:
        await self._apply(temp_m=int(value))


class QuietCoolTempLowNumber(_QuietCoolThresholdBase):
    """Low temperature threshold for TH smart mode (fan turns off below this)."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = 40
    # Raised 90 -> 115: device encodes temp_l up to 110/120 like med/high
    # (app slider caps at 90).
    _attr_native_max_value = 115
    _attr_native_step = 1
    _attr_name = "Low Temp Threshold"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "temp_l")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.temp_l)

    async def async_set_native_value(self, value: float) -> None:
        await self._apply(temp_l=int(value))


class QuietCoolHumHighNumber(_QuietCoolThresholdBase):
    """Humidity turn-OFF threshold for TH smart mode.

    App label "Turn Fan Off" (factory 90): the fan turns OFF at/above this RH — a
    high-humidity cutout checked FIRST, so it won't run when ventilating can't help.
    Device field GetHum_H.
    """

    _attr_device_class = NumberDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_name = "Humidity Off Threshold"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "hum_h")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.hum_h)

    async def async_set_native_value(self, value: float) -> None:
        await self._apply(hum_h=int(value))


class QuietCoolHumLowNumber(_QuietCoolThresholdBase):
    """Humidity turn-ON threshold for TH smart mode.

    App label "Turn Fan On" (factory 70): the fan turns ON above this RH regardless
    of temperature, running at the humidity speed range. Device field GetHum_L; the
    device stores 255 when the feature is OFF. We surface 255 as None (unknown) rather
    than a bogus 255%, and writing any 10–100 value enables it.
    """

    _attr_device_class = NumberDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 10
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_name = "Humidity On Threshold"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "hum_l")

    @property
    def native_value(self) -> float | None:
        params = self.coordinator.fan_parameters
        if params is None or params.hum_l == HUM_L_DISABLED:
            return None
        return float(params.hum_l)

    async def async_set_native_value(self, value: float) -> None:
        await self._apply(hum_l=int(value))

class _QuietCoolTimerBase(_QuietCoolThresholdBase):
    """Base class for the timer-duration number entities (Hours / Minutes)."""

    def _write_timer(self, **overrides: Any) -> Any:
        """Build a coroutine that saves the timer duration via SetTime.

        Callers pass only the field they are changing (hours or minutes); the
        rest — including the timer speed — come from the current fan_parameters
        so unchanged values are preserved and the fan is not started.
        """
        params = self.coordinator.fan_parameters
        assert params is not None
        protocol = self.coordinator.fan_info.protocol
        merged = {
            "hours": params.timer_hour,
            "minutes": params.timer_minute,
            "speed": params.timer_range,
        }
        merged.update(overrides)
        return lambda client: api.set_timer(client, protocol=protocol, **merged)


class QuietCoolTimerHoursNumber(_QuietCoolTimerBase):
    """Timer duration — hours the fan runs in Timer mode before shutting off."""

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_native_min_value = 0
    _attr_native_max_value = 23
    _attr_native_step = 1
    _attr_icon = "mdi:timer-outline"
    _attr_name = "Timer Hours"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "timer_hour")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.timer_hour)

    async def async_set_native_value(self, value: float) -> None:
        params = self.coordinator.fan_parameters
        if params is None:
            return
        new_val = int(value)
        await self.coordinator.async_execute(self._write_timer(hours=new_val))
        self.coordinator.fan_parameters = dataclasses.replace(
            params, timer_hour=new_val
        )
        self.async_write_ha_state()


class QuietCoolTimerMinutesNumber(_QuietCoolTimerBase):
    """Timer duration — minutes the fan runs in Timer mode before shutting off."""

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_max_value = 59
    _attr_native_step = 1
    _attr_icon = "mdi:timer-outline"
    _attr_name = "Timer Minutes"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "timer_minute")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_parameters is None:
            return None
        return float(self.coordinator.fan_parameters.timer_minute)

    async def async_set_native_value(self, value: float) -> None:
        params = self.coordinator.fan_parameters
        if params is None:
            return
        new_val = int(value)
        await self.coordinator.async_execute(self._write_timer(minutes=new_val))
        self.coordinator.fan_parameters = dataclasses.replace(
            params, timer_minute=new_val
        )
        self.async_write_ha_state()

