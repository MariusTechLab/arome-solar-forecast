"""Automatic correction from the actual production of the last days.

Every night, the archived forecasts of the last AUTO_DAYS days (Open-Meteo
Previous Runs API, latest run before each period) are compared with the
statistics of the production sensor, by slots of 15, 30 or 60 minutes. Below
one hour, the 15-minute forecasts and the 5-minute statistics are used; the
recorder keeps those for its retention period (10 days by default). See
forecast.auto_correction for the learning rule.
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
from .forecast import AutoCorrection, PvArray, PvSystem, aggregate, auto_correction, compute_curve

_LOGGER = logging.getLogger(__name__)


async def _fetch_archive(session: aiohttp.ClientSession, latitude: float, longitude: float, model: str,
                         array: PvArray, start: date, end: date, block: str) -> dict:
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "models": model,
        block: "global_tilted_irradiance,temperature_2m",
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


async def _actual_kwh(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime,
                      slot_minutes: int) -> dict[datetime, float]:
    """Energy per slot from the recorder statistics of an energy or power sensor.

    Hourly statistics for 60-minute slots, 5-minute statistics below; a slot
    with a missing 5-minute row (gap in the data) is dropped.
    """
    state = hass.states.get(entity_id)
    is_power = bool(state and state.attributes.get("device_class") == "power")
    kind = "mean" if is_power else "change"
    period, minutes = ("hour", 60) if slot_minutes == 60 else ("5minute", 5)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {entity_id}, period,
        {"energy": "kWh", "power": "kW"}, {kind},
    )
    rows: dict[datetime, float] = {}
    for row in stats.get(entity_id, []):
        value = row.get(kind)
        if value is None:
            continue
        begin = row["start"]
        begin = datetime.fromtimestamp(begin, timezone.utc) if isinstance(begin, (int, float)) else begin
        energy = float(value) * minutes / 60 if is_power else float(value)
        rows[begin.astimezone(timezone.utc)] = max(energy, 0.0)
    if slot_minutes == 60:
        return rows
    counts = aggregate({t: 1.0 for t in rows}, slot_minutes)
    expected = slot_minutes // minutes
    return {t: v for t, v in aggregate(rows, slot_minutes).items() if counts.get(t) == expected}


async def compute_correction(hass: HomeAssistant, session: aiohttp.ClientSession, latitude: float,
                             longitude: float, model: str, system: PvSystem, manual: tuple[float, ...],
                             entity_id: str, slot_minutes: int = 60) -> AutoCorrection:
    tz = dt_util.get_default_time_zone()
    today = dt_util.now().date()
    first, last = today - timedelta(days=AUTO_DAYS), today - timedelta(days=1)
    block, step = ("hourly", timedelta(hours=1)) if slot_minutes == 60 else ("minutely_15", timedelta(minutes=15))
    archives = await asyncio.gather(*(
        _fetch_archive(session, latitude, longitude, model, array, first, last, block) for array in system.arrays
    ))
    # Forecast without any shading: the factors are learned from scratch on top of it.
    raw_system = replace(system, shading=(1.0,) * 24, solar_shading=None)
    curve = compute_curve(raw_system, archives, tz, block_name=block, step=step)
    hours = step.total_seconds() / 3600
    forecast = aggregate({s.start: s.kw * hours for s in curve}, slot_minutes)
    start = datetime.combine(first, datetime.min.time(), tz)
    end = datetime.combine(today, datetime.min.time(), tz)
    actual = await _actual_kwh(hass, entity_id, start, end, slot_minutes)
    result = auto_correction(forecast, actual, manual, sum(a.kwp for a in system.arrays), tz, slot_minutes)
    _LOGGER.debug("Auto correction (%s min) from %s days: %s", slot_minutes, result.days, result.factors)
    return result
