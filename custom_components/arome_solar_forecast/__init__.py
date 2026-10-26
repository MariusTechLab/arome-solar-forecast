"""AROME Solar Forecast: PV forecast from Météo-France AROME via Open-Meteo."""

from __future__ import annotations

from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import AromeForecastCoordinator
from .forecast import half_hours, quarter_hours

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AromeConfigEntry = ConfigEntry[AromeForecastCoordinator]

SERVICE_GET_FORECAST = "get_forecast"
SERVICE_SCHEMA = vol.Schema({
    vol.Optional("config_entry_id"): cv.string,
    vol.Optional("days", default=2): vol.All(vol.Coerce(int), vol.Range(min=1, max=7)),
    vol.Optional("resolution", default="30min"): vol.In(["15min", "30min"]),
})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the get_forecast action."""

    async def get_forecast(call: ServiceCall) -> ServiceResponse:
        entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)]
        entry_id = call.data.get("config_entry_id")
        if entry_id:
            entries = [e for e in entries if e.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_entry")
        coordinator: AromeForecastCoordinator = entries[0].runtime_data
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

    hass.services.async_register(DOMAIN, SERVICE_GET_FORECAST, get_forecast, schema=SERVICE_SCHEMA,
                                 supports_response=SupportsResponse.ONLY)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AromeConfigEntry) -> bool:
    coordinator = AromeForecastCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: AromeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: AromeConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
