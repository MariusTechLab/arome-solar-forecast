"""AROME Solar Forecast: PV forecast from Météo-France AROME via Open-Meteo."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import AUTO_RUN_TIME, DOMAIN
from .coordinator import AromeForecastCoordinator
from .forecast import QUARTER, best_window, half_hours, quarter_hours

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AromeConfigEntry = ConfigEntry[AromeForecastCoordinator]

SERVICE_GET_FORECAST = "get_forecast"
SERVICE_SCHEMA = vol.Schema({
    vol.Optional("config_entry_id"): cv.string,
    vol.Optional("days", default=2): vol.All(vol.Coerce(int), vol.Range(min=1, max=7)),
    vol.Optional("resolution", default="30min"): vol.In(["15min", "30min"]),
})

SERVICE_FIND_BEST_WINDOW = "find_best_window"
WINDOW_SCHEMA = vol.Schema({
    vol.Optional("config_entry_id"): cv.string,
    vol.Required("duration"): vol.All(vol.Coerce(int), vol.Range(min=15, max=720)),
    vol.Optional("day", default="today"): vol.In(["today", "tomorrow"]),
    vol.Optional("power_kw"): vol.All(vol.Coerce(float), vol.Range(min=0.01, max=1000)),
    vol.Optional("base_load_kw", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
    vol.Optional("after"): cv.time,
    vol.Optional("before"): cv.time,
})


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> AromeForecastCoordinator:
    entries = list(hass.config_entries.async_loaded_entries(DOMAIN))
    if entry_id := call.data.get("config_entry_id"):
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries or not entries[0].runtime_data.data:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_entry")
    return entries[0].runtime_data


def _round_up_to_quarter(when: datetime) -> datetime:
    floor = when.replace(minute=when.minute - when.minute % 15, second=0, microsecond=0)
    return floor if floor == when else floor + QUARTER


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions."""

    async def get_forecast(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        tz = dt_util.get_default_time_zone()
        today = dt_util.now().date()
        builder = quarter_hours if call.data["resolution"] == "15min" else half_hours
        forecast = []
        for offset in range(call.data["days"]):
            forecast += builder(coordinator.data.curve, today + timedelta(days=offset), tz)
        return {
            "model": coordinator.model,
            "updated": coordinator.data.fetched_at.isoformat(),
            "forecast": forecast,
        }

    async def find_best_window(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        tz = dt_util.get_default_time_zone()
        now = dt_util.now()
        day = now.date() + timedelta(days=1 if call.data["day"] == "tomorrow" else 0)
        earliest = datetime.combine(day, call.data.get("after", time(0)), tz)
        latest = (datetime.combine(day, call.data["before"], tz) if "before" in call.data
                  else datetime.combine(day + timedelta(days=1), time(0), tz))
        earliest = max(earliest, _round_up_to_quarter(now))
        steps = -(-call.data["duration"] // 15)
        window = best_window(coordinator.data.curve, timedelta(minutes=call.data["duration"]),
                             earliest, latest, call.data.get("power_kw"), call.data["base_load_kw"])
        return {
            "found": window is not None,
            "start": window.start.astimezone(tz).isoformat() if window else None,
            "end": window.end.astimezone(tz).isoformat() if window else None,
            "duration_minutes": steps * 15,
            "usable_energy_kwh": window.usable_kwh if window else 0.0,
            "solar_energy_kwh": window.solar_kwh if window else 0.0,
            "updated": coordinator.data.fetched_at.isoformat(),
        }

    hass.services.async_register(DOMAIN, SERVICE_GET_FORECAST, get_forecast, schema=SERVICE_SCHEMA,
                                 supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, SERVICE_FIND_BEST_WINDOW, find_best_window, schema=WINDOW_SCHEMA,
                                 supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AromeConfigEntry) -> bool:
    coordinator = AromeForecastCoordinator(hass, entry)
    await coordinator.async_load_correction()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    if coordinator.auto_enabled:
        hour, minute = AUTO_RUN_TIME
        entry.async_on_unload(async_track_time_change(
            hass, coordinator.async_update_correction, hour=hour, minute=minute, second=0))
        entry.async_create_background_task(hass, coordinator.async_update_correction(),
                                           f"{DOMAIN} automatic correction")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: AromeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AromeConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
