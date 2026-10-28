"""Data update coordinator: one Open-Meteo request per roof plane."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_URL,
    ARRAY_KEYS,
    CONF_AUTO_CORRECTION,
    CONF_CORRECTION_MINUTES,
    CONF_CELL_HEATING,
    CONF_INVERTER_EFFICIENCY,
    CONF_INVERTER_KW,
    CONF_MODEL,
    CONF_PRODUCTION_ENTITY,
    CONF_SCAN_MINUTES,
    CONF_SHADING,
    CONF_SYSTEM_EFFICIENCY,
    CONF_TEMP_COEFFICIENT,
    DEFAULT_AZIMUTH,
    DEFAULT_CELL_HEATING,
    DEFAULT_CORRECTION_MINUTES,
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
from .correction import compute_correction
from .forecast import (
    AutoCorrection,
    PvArray,
    PvSystem,
    Slot,
    compute_curve,
    has_irradiance,
    parse_shading,
    request_params,
)

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
        self.manual_shading = self.system.shading
        self.production_entity: str | None = entry_value(entry, CONF_PRODUCTION_ENTITY) or None
        self.auto_enabled = bool(entry_value(entry, CONF_AUTO_CORRECTION, False)) and bool(self.production_entity)
        self.correction_minutes = int(entry_value(entry, CONF_CORRECTION_MINUTES, DEFAULT_CORRECTION_MINUTES))
        self.correction: AutoCorrection | None = None
        self.correction_updated: datetime | None = None
        self.correction_error: str | None = None
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._session = async_get_clientsession(hass)

    def _apply_correction(self, correction: AutoCorrection, updated: datetime) -> None:
        self.correction = correction
        self.correction_updated = updated
        self.system = replace(self.system, solar_shading=correction.factors)

    async def async_load_correction(self) -> None:
        """Use the factors saved by the last run until the next one."""
        if not self.auto_enabled:
            return
        saved = await self._store.async_load()
        # Factors saved before 1.4.0 (by hour only, or by local hour before 1.3.1) are
        # ignored: they are recomputed at startup.
        minutes = self.correction_minutes
        if (saved and saved.get("basis") == "utc" and saved.get("slot_minutes") == minutes
                and saved.get("entity") == self.production_entity
                and saved.get("manual") == list(self.manual_shading)
                and len(saved.get("factors", [])) == 1440 // minutes):
            slots = {int(i): v for i, v in saved.get("slots", {}).items()}
            updated = dt_util.parse_datetime(saved["updated"]) or dt_util.utcnow()
            correction = AutoCorrection(tuple(saved["factors"]), slots, saved.get("days", 0), minutes)
            self._apply_correction(correction, updated)

    async def async_update_correction(self, *_: Any) -> None:
        """Recompute the factors from the last days of production."""
        if not self.auto_enabled or not self.production_entity:
            return
        try:
            result = await compute_correction(self.hass, self._session, self.latitude, self.longitude,
                                              self.model, self.system, self.manual_shading,
                                              self.production_entity, self.correction_minutes)
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, HomeAssistantError) as err:
            self.correction_error = str(err)
            _LOGGER.warning("Automatic correction not updated, keeping the previous factors: %s", err)
            return
        self.correction_error = None
        now = dt_util.utcnow()
        self._apply_correction(result, now)
        await self._store.async_save({
            "basis": "utc",
            "slot_minutes": result.slot_minutes,
            "entity": self.production_entity,
            "manual": list(self.manual_shading),
            "factors": list(result.factors),
            "slots": {str(i): v for i, v in result.slots.items()},
            "days": result.days,
            "updated": now.isoformat(),
        })
        await self.async_request_refresh()

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
