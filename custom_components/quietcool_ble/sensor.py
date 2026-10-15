"""QuietCool BLE sensor entities — temperature, humidity, timer, and diagnostic.

Temperature formula (confirmed from emerose/quietcool source):
  temperature_fahrenheit = Temp_Sample / 10
  e.g. Temp_Sample 921 → 92.1°F (normal attic in spring)

Humidity: Humidity_Sample is a direct integer percentage (no divisor).
  e.g. Humidity_Sample 24 → 24% RH
  (The /10 divisor that applies to temperature does NOT apply here —
  hardware-confirmed: 2.4% is impossible, 24% matches real attic conditions.)
"""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import api
from .const import DOMAIN
from .coordinator import QuietCoolBLECoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: QuietCoolBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            QuietCoolFanSpeedSensor(coordinator),
            QuietCoolTemperatureSensor(coordinator),
            QuietCoolHumiditySensor(coordinator),
            QuietCoolRemainingTimerSensor(coordinator),
            QuietCoolProtectTempSensor(coordinator),
        ]
    )


class _QuietCoolSensorBase(CoordinatorEntity[QuietCoolBLECoordinator], SensorEntity):
    """Base class for QuietCool sensors."""

    _attr_has_entity_name = True

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


class QuietCoolFanSpeedSensor(_QuietCoolSensorBase):
    """Physical fan speed: Off / Low / Medium / High.

    Distinct from the Mode select (Idle / Timer / TH). In TH mode the
    controller automatically cycles the fan on and off as thresholds are
    crossed; this sensor reflects what the blades are actually doing now.
    Medium only ever appears on 3-speed fans that report it.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_name = "Fan Speed"
    _attr_options = ["Off", "Low", "Medium", "High"]

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "fan_speed")

    @property
    def available(self) -> bool:
        return self.coordinator.fan_state is not None

    @property
    def native_value(self) -> str | None:
        return api.fan_speed_label(self.coordinator.fan_state)


class QuietCoolTemperatureSensor(_QuietCoolSensorBase):
    """Attic temperature sensor. Value = Temp_Sample / 10 in °F."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Temperature"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "temperature")

    @property
    def available(self) -> bool:
        return self.coordinator.fan_state is not None

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.temp_fahrenheit


class QuietCoolHumiditySensor(_QuietCoolSensorBase):
    """Attic humidity sensor. Value = Humidity_Sample direct integer in %."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Humidity"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "humidity")

    @property
    def available(self) -> bool:
        return self.coordinator.fan_state is not None

    @property
    def native_value(self) -> float | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.humidity_percent


class QuietCoolRemainingTimerSensor(_QuietCoolSensorBase):
    """Remaining timer countdown in seconds. Non-zero only in Timer mode."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Timer Remaining"

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "timer_remaining")

    @property
    def available(self) -> bool:
        return self.coordinator.fan_state is not None

    @property
    def native_value(self) -> int | None:
        if self.coordinator.fan_state is None:
            return None
        return self.coordinator.fan_state.remain_seconds


class QuietCoolProtectTempSensor(_QuietCoolSensorBase):
    """Overtemperature safety cutoff in °F. Read from firmware; never changes."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "Protect Temperature"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: QuietCoolBLECoordinator) -> None:
        super().__init__(coordinator, "protect_temp")

    @property
    def available(self) -> bool:
        return self.coordinator.fan_version is not None

    @property
    def native_value(self) -> int | None:
        if self.coordinator.fan_version is None:
            return None
        return self.coordinator.fan_version.protect_temp
