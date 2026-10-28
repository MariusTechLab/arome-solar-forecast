"""Forecast sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import AromeConfigEntry
from .const import ATTRIBUTION, DOMAIN, VERSION
from .coordinator import AromeForecastCoordinator, ForecastData
from .forecast import covers_day, local_slot_label, day_energy, energy_between, half_hours, peak, power_at, quarter_hours


@dataclass(frozen=True, kw_only=True)
class AromeSensorDescription(SensorEntityDescription):
    value: Callable[[ForecastData, datetime], Any]
    attributes: Callable[[ForecastData, datetime], dict[str, Any]] | None = None
    time_dependent: bool = False


def _tz():
    return dt_util.get_default_time_zone()


def _day(now: datetime, offset: int = 0):
    return (now + timedelta(days=offset)).date()


def _energy(offset: int) -> Callable[[ForecastData, datetime], float]:
    return lambda d, now: round(day_energy(d.curve, _day(now, offset), _tz()), 2)


def _day_attributes(offset: int) -> Callable[[ForecastData, datetime], dict[str, Any]]:
    def build(d: ForecastData, now: datetime) -> dict[str, Any]:
        day = _day(now, offset)
        return {
            "detailedForecast": half_hours(d.curve, day, _tz()),
            "detailedForecast15": quarter_hours(d.curve, day, _tz()),
            "complete": covers_day(d.curve, day, _tz()),
        }
    return build


def _hour_energy(offset_hours: int) -> Callable[[ForecastData, datetime], float]:
    def value(d: ForecastData, now: datetime) -> float:
        start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset_hours)
        return round(energy_between(d.curve, start, start + timedelta(hours=1)), 3)
    return value


def _remaining(d: ForecastData, now: datetime) -> float:
    midnight = dt_util.start_of_local_day(now) + timedelta(days=1)
    return round(energy_between(d.curve, now, midnight), 2)


def _peak_power(offset: int) -> Callable[[ForecastData, datetime], int]:
    def value(d: ForecastData, now: datetime) -> int:
        slot = peak(d.curve, _day(now, offset), _tz())
        return round(slot.kw * 1000) if slot else 0
    return value


def _peak_time(offset: int) -> Callable[[ForecastData, datetime], datetime | None]:
    def value(d: ForecastData, now: datetime) -> datetime | None:
        slot = peak(d.curve, _day(now, offset), _tz())
        return slot.start if slot else None
    return value


def _power(minutes: int) -> Callable[[ForecastData, datetime], int]:
    return lambda d, now: round(power_at(d.curve, now + timedelta(minutes=minutes)) * 1000)


ENERGY = {"native_unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
          "device_class": SensorDeviceClass.ENERGY, "suggested_display_precision": 2}
POWER = {"native_unit_of_measurement": UnitOfPower.WATT, "device_class": SensorDeviceClass.POWER,
         "state_class": SensorStateClass.MEASUREMENT, "suggested_display_precision": 0}

SENSORS: tuple[AromeSensorDescription, ...] = (
    AromeSensorDescription(key="energy_today", translation_key="energy_today", **ENERGY,
                           value=_energy(0), attributes=_day_attributes(0), time_dependent=True),
    AromeSensorDescription(key="energy_tomorrow", translation_key="energy_tomorrow", **ENERGY,
                           value=_energy(1), attributes=_day_attributes(1), time_dependent=True),
    AromeSensorDescription(key="energy_remaining_today", translation_key="energy_remaining_today", **ENERGY,
                           value=_remaining, time_dependent=True),
    AromeSensorDescription(key="energy_current_hour", translation_key="energy_current_hour",
                           **{**ENERGY, "suggested_display_precision": 3},
                           value=_hour_energy(0), time_dependent=True),
    AromeSensorDescription(key="energy_next_hour", translation_key="energy_next_hour",
                           **{**ENERGY, "suggested_display_precision": 3},
                           value=_hour_energy(1), time_dependent=True),
    AromeSensorDescription(key="power_now", translation_key="power_now", **POWER,
                           value=_power(0), time_dependent=True),
    AromeSensorDescription(key="power_in_30_minutes", translation_key="power_in_30_minutes", **POWER,
                           value=_power(30), time_dependent=True),
    AromeSensorDescription(key="power_in_1_hour", translation_key="power_in_1_hour", **POWER,
                           value=_power(60), time_dependent=True),
    AromeSensorDescription(key="peak_power_today", translation_key="peak_power_today",
                           native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER,
                           suggested_display_precision=0, value=_peak_power(0), time_dependent=True),
    AromeSensorDescription(key="peak_time_today", translation_key="peak_time_today",
                           device_class=SensorDeviceClass.TIMESTAMP, value=_peak_time(0), time_dependent=True),
    AromeSensorDescription(key="peak_power_tomorrow", translation_key="peak_power_tomorrow",
                           native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER,
                           suggested_display_precision=0, value=_peak_power(1), time_dependent=True),
    AromeSensorDescription(key="peak_time_tomorrow", translation_key="peak_time_tomorrow",
                           device_class=SensorDeviceClass.TIMESTAMP, value=_peak_time(1), time_dependent=True),
    AromeSensorDescription(key="last_update", translation_key="last_update",
                           device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC,
                           value=lambda d, now: d.fetched_at,
                           attributes=lambda d, now: {"stale": d.stale}),
)


CORRECTION = AromeSensorDescription(
    key="correction", translation_key="correction", entity_category=EntityCategory.DIAGNOSTIC,
    suggested_display_precision=2, value=lambda d, now: None)


async def async_setup_entry(hass: HomeAssistant, entry: AromeConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [AromeSensor(coordinator, entry, description) for description in SENSORS]
    if coordinator.auto_enabled:
        entities.append(AromeCorrectionSensor(coordinator, entry, CORRECTION))
    async_add_entities(entities)


class AromeSensor(CoordinatorEntity[AromeForecastCoordinator], SensorEntity):
    """A value computed from the forecast curve."""

    entity_description: AromeSensorDescription
    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    # Large forecast lists are exposed to templates but kept out of the database.
    _unrecorded_attributes = frozenset({"detailedForecast", "detailedForecast15"})

    def __init__(self, coordinator: AromeForecastCoordinator, entry: AromeConfigEntry,
                 description: AromeSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Météo-France / Open-Meteo",
            model=coordinator.model,
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://open-meteo.com/en/docs/meteofrance-api",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.time_dependent:
            # Values that depend on the current time are refreshed every minute.
            self.async_on_remove(async_track_time_change(self.hass, self._minute_tick, second=0))

    @callback
    def _minute_tick(self, _now: datetime) -> None:
        if self.coordinator.data:
            self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value(self.coordinator.data, dt_util.now())

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.coordinator.data or not self.entity_description.attributes:
            return None
        return self.entity_description.attributes(self.coordinator.data, dt_util.now())


class AromeCorrectionSensor(AromeSensor):
    """Automatic correction: mean daytime factor, details in the attributes."""

    @property
    def native_value(self) -> float | None:
        correction = self.coordinator.correction
        if not correction or not correction.slots:
            return None
        values = [correction.factors[i] for i in correction.slots]
        return round(sum(values) / len(values), 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        c = self.coordinator
        attributes: dict[str, Any] = {
            "production_entity": c.production_entity,
            "manual_factors": {f"{h:02d}": f for h, f in enumerate(c.manual_shading) if f != 1.0},
        }
        if c.correction:
            # Learned by UTC slot (sun position); shown with today's local clock times.
            today, tz, minutes = dt_util.now().date(), dt_util.get_default_time_zone(), c.correction.slot_minutes
            local = {i: local_slot_label(i, today, tz, minutes) for i in c.correction.slots}
            order = sorted(local, key=local.get)
            attributes.update({
                "slot_minutes": minutes,
                "days": c.correction.days,
                "factors": {local[i]: c.correction.factors[i] for i in order},
                "slots": {local[i]: c.correction.slots[i] for i in order},
                "updated": c.correction_updated.isoformat() if c.correction_updated else None,
            })
        if c.correction_error:
            attributes["last_error"] = c.correction_error
        return attributes
