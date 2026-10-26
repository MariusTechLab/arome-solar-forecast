"""Data update coordinator: one Open-Meteo request per roof plane."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_URL,
    ARRAY_KEYS,
    CONF_CELL_HEATING,
    CONF_INVERTER_EFFICIENCY,
    CONF_INVERTER_KW,
    CONF_MODEL,
    CONF_SCAN_MINUTES,
    CONF_SHADING,
    CONF_SYSTEM_EFFICIENCY,
    CONF_TEMP_COEFFICIENT,
    DEFAULT_AZIMUTH,
    DEFAULT_CELL_HEATING,
    DEFAULT_INVERTER_EFFICIENCY,
    DEFAULT_MODEL,
    DEFAULT_SCAN_MINUTES,
    DEFAULT_SYSTEM_EFFICIENCY,
    DEFAULT_TEMP_COEFFICIENT,
    DEFAULT_TILT,
    DOMAIN,
    MODELS,
    STALE_AFTER_HOURS,
    VERSION,
)
from .forecast import PvArray, PvSystem, Slot, compute_curve, has_irradiance, parse_shading, request_params

_LOGGER = logging.getLogger(__name__)


def entry_value(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Options override the data entered in the initial flow."""
    return entry.options.get(key, entry.data.get(key, default))


def system_from_entry(entry: ConfigEntry) -> PvSystem:
    arrays = []
    for kwp_key, tilt_key, azimuth_key in ARRAY_KEYS:
        kwp = float(entry_value(entry, kwp_key, 0) or 0)
        if kwp > 0:
            arrays.append(PvArray(kwp, float(entry_value(entry, tilt_key, DEFAULT_TILT)),
                                  float(entry_value(entry, azimuth_key, DEFAULT_AZIMUTH))))
    inverter = float(entry_value(entry, CONF_INVERTER_KW, 0) or 0)
    return PvSystem(
        arrays=tuple(arrays),
        inverter_kw=inverter or None,
        system_efficiency=float(entry_value(entry, CONF_SYSTEM_EFFICIENCY, DEFAULT_SYSTEM_EFFICIENCY)),
        inverter_efficiency=float(entry_value(entry, CONF_INVERTER_EFFICIENCY, DEFAULT_INVERTER_EFFICIENCY)),
        temp_coefficient=float(entry_value(entry, CONF_TEMP_COEFFICIENT, DEFAULT_TEMP_COEFFICIENT)),
        cell_heating=float(entry_value(entry, CONF_CELL_HEATING, DEFAULT_CELL_HEATING)),
        shading=parse_shading(entry_value(entry, CONF_SHADING, "")),
    )


async def fetch_forecast(session: aiohttp.ClientSession, latitude: float, longitude: float,
                         model: str, array: PvArray) -> dict:
    params = request_params(latitude, longitude, model, array, MODELS.get(model, 2))
    headers = {"User-Agent": f"HomeAssistant-AromeSolarForecast/{VERSION}"}
    async with asyncio.timeout(30):
        async with session.get(API_URL, params=params, headers=headers) as response:
            data = await response.json(content_type=None)
            if response.status != 200 or data.get("error"):
                raise UpdateFailed(f"Open-Meteo: {data.get('reason', response.status)}")
            return data


@dataclass
class ForecastData:
    curve: list[Slot]
    fetched_at: datetime
    stale: bool = False


class AromeForecastCoordinator(DataUpdateCoordinator[ForecastData]):
    """Fetches the forecast and computes the PV curve."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = int(entry_value(entry, CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES))
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(minutes=minutes))
        self.latitude = float(entry_value(entry, "latitude", hass.config.latitude))
        self.longitude = float(entry_value(entry, "longitude", hass.config.longitude))
        self.model = str(entry_value(entry, CONF_MODEL, DEFAULT_MODEL))
        self.system = system_from_entry(entry)
        self._session = async_get_clientsession(hass)

    async def _async_update_data(self) -> ForecastData:
        try:
            responses = await asyncio.gather(*(
                fetch_forecast(self._session, self.latitude, self.longitude, self.model, array)
                for array in self.system.arrays
            ))
            if not has_irradiance(responses[0]):
                raise UpdateFailed(f"no irradiance from model {self.model} at this location")
        except (aiohttp.ClientError, TimeoutError, UpdateFailed, ValueError) as err:
            previous = self.data
            if previous and dt_util.utcnow() - previous.fetched_at < timedelta(hours=STALE_AFTER_HOURS):
                _LOGGER.warning("Open-Meteo update failed, keeping the previous forecast: %s", err)
                return ForecastData(previous.curve, previous.fetched_at, stale=True)
            raise UpdateFailed(str(err)) from err
        curve = compute_curve(self.system, responses, dt_util.get_default_time_zone())
        return ForecastData(curve, dt_util.utcnow())
