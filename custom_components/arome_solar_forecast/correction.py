"""Automatic hourly correction from the actual production of the last days.

Every night, the archived forecasts of the last AUTO_DAYS days (Open-Meteo
Previous Runs API, latest run before each hour) are compared with the hourly
statistics of the production sensor. See forecast.auto_correction for the
learning rule.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import aiohttp
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import ARCHIVE_URL, AUTO_DAYS, VERSION
from .forecast import AutoCorrection, PvArray, PvSystem, auto_correction, compute_curve

_LOGGER = logging.getLogger(__name__)


async def _fetch_archive(session: aiohttp.ClientSession, latitude: float, longitude: float, model: str,
                         array: PvArray, start: date, end: date) -> dict:
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "models": model,
        "hourly": "global_tilted_irradiance,temperature_2m",
        "tilt": f"{array.tilt:g}",
        "azimuth": f"{array.openmeteo_azimuth:g}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "UTC",
    }
    headers = {"User-Agent": f"HomeAssistant-AromeSolarForecast/{VERSION}"}
    async with asyncio.timeout(60):
        async with session.get(ARCHIVE_URL, params=params, headers=headers) as response:
            data = await response.json(content_type=None)
            if response.status != 200 or data.get("error"):
                raise ValueError(f"Open-Meteo archive: {data.get('reason', response.status)}")
            return data


async def _actual_kwh(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> dict[datetime, float]:
    """Hourly energy from the recorder statistics of an energy or power sensor."""
    state = hass.states.get(entity_id)
    is_power = bool(state and state.attributes.get("device_class") == "power")
    kind = "mean" if is_power else "change"
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {entity_id}, "hour",
        {"energy": "kWh", "power": "kW"}, {kind},
    )
    out: dict[datetime, float] = {}
    for row in stats.get(entity_id, []):
        value = row.get(kind)
        if value is None:
            continue
        begin = row["start"]
        begin = datetime.fromtimestamp(begin, timezone.utc) if isinstance(begin, (int, float)) else begin
        out[begin.astimezone(timezone.utc)] = max(float(value), 0.0)
    return out


async def compute_correction(hass: HomeAssistant, session: aiohttp.ClientSession, latitude: float,
                             longitude: float, model: str, system: PvSystem, manual: tuple[float, ...],
                             entity_id: str) -> AutoCorrection:
    tz = dt_util.get_default_time_zone()
    today = dt_util.now().date()
    first, last = today - timedelta(days=AUTO_DAYS), today - timedelta(days=1)
    archives = await asyncio.gather(*(
        _fetch_archive(session, latitude, longitude, model, array, first, last) for array in system.arrays
    ))
    # Forecast without any shading: the factors are learned from scratch on top of it.
    raw_system = replace(system, shading=(1.0,) * 24, solar_shading=None)
    hourly = compute_curve(raw_system, archives, tz, block_name="hourly", step=timedelta(hours=1))
    forecast = {s.start.astimezone(timezone.utc): s.kw for s in hourly}
    start = datetime.combine(first, datetime.min.time(), tz)
    end = datetime.combine(today, datetime.min.time(), tz)
    actual = await _actual_kwh(hass, entity_id, start, end)
    result = auto_correction(forecast, actual, manual, sum(a.kwp for a in system.arrays), tz)
    _LOGGER.debug("Auto correction from %s days: %s", result.days, result.factors)
    return result
