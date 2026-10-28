"""PV forecast model: Open-Meteo irradiance to AC power.

Pure Python, no Home Assistant import, so it can be tested on its own.
Same formula as the YAML package (see docs/how-it-works.md).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo

QUARTER = timedelta(minutes=15)


@dataclass(frozen=True)
class PvArray:
    """One roof plane."""

    kwp: float
    tilt: float = 30.0
    azimuth: float = 180.0  # compass: 0 north, 90 east, 180 south, 270 west

    @property
    def openmeteo_azimuth(self) -> float:
        """Open-Meteo convention: 0 south, -90 east, 90 west."""
        value = (self.azimuth - 180.0) % 360.0
        return value - 360.0 if value > 180.0 else value


@dataclass(frozen=True)
class PvSystem:
    """Roof planes and conversion parameters."""

    arrays: tuple[PvArray, ...]
    inverter_kw: float | None = None
    system_efficiency: float = 0.98
    inverter_efficiency: float = 0.96
    temp_coefficient: float = -0.004
    cell_heating: float = 0.03
    shading: tuple[float, ...] = field(default_factory=lambda: (1.0,) * 24)  # by local hour (manual)
    # Learned factors by UTC hour, i.e. by sun position, so that they stay right
    # across daylight saving time changes. When set, they replace `shading`.
    solar_shading: tuple[float, ...] | None = None


@dataclass(frozen=True)
class Slot:
    """One quarter hour of forecast."""

    start: datetime  # aware, start of the quarter hour
    kw: float


def request_params(latitude: float, longitude: float, model: str, array: PvArray,
                   forecast_days: int) -> dict[str, str]:
    """Query parameters of the Open-Meteo forecast API for one roof plane."""
    return {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "models": model,
        "minutely_15": "global_tilted_irradiance,temperature_2m",
        "tilt": f"{array.tilt:g}",
        "azimuth": f"{array.openmeteo_azimuth:g}",
        "forecast_days": str(forecast_days),
        "timezone": "UTC",
    }


def has_irradiance(response: dict) -> bool:
    """True when the model returned at least one irradiance value."""
    values = (response.get("minutely_15") or {}).get("global_tilted_irradiance") or []
    return any(v is not None for v in values)


def compute_curve(system: PvSystem, responses: Sequence[dict], tz: tzinfo | None = None,
                  block_name: str = "minutely_15", step: timedelta = QUARTER) -> list[Slot]:
    """AC power per period from one API response per roof plane.

    Radiation is the mean of the preceding period, so a value stamped T
    describes the period starting at T - step (15 minutes, or one hour for the
    "hourly" block of archived forecasts).
    """
    if len(responses) != len(system.arrays):
        raise ValueError("one response per roof plane is required")
    series = []
    for response in responses:
        block = response.get(block_name) or {}
        times = block.get("time") or []
        series.append((
            {t: g for t, g in zip(times, block.get("global_tilted_irradiance") or [])},
            {t: c for t, c in zip(times, block.get("temperature_2m") or [])},
        ))
    base_times = (responses[0].get(block_name) or {}).get("time") or []
    slots: list[Slot] = []
    for t in base_times:
        start = datetime.fromisoformat(t).replace(tzinfo=timezone.utc) - step
        utc_hour = start.hour
        if tz is not None:
            start = start.astimezone(tz)
        dc = 0.0
        for array, (gti, temp) in zip(system.arrays, series):
            g = gti.get(t)
            g = float(g) if g is not None else 0.0
            ta = temp.get(t)
            ta = float(ta) if ta is not None else 15.0
            cell = ta + system.cell_heating * g
            dc += array.kwp * g / 1000 * (1 + system.temp_coefficient * (cell - 25)) * system.system_efficiency
        factor = system.solar_shading[utc_hour] if system.solar_shading else system.shading[start.hour]
        ac = dc * factor * system.inverter_efficiency
        if system.inverter_kw:
            ac = min(ac, system.inverter_kw)
        slots.append(Slot(start, round(max(ac, 0.0), 4)))
    return slots


def _local_date(slot: Slot, tz) -> date:
    return slot.start.astimezone(tz).date() if tz else slot.start.date()


def day_slots(curve: Iterable[Slot], day: date, tz=None) -> list[Slot]:
    return [s for s in curve if _local_date(s, tz) == day]


def energy_between(curve: Iterable[Slot], start: datetime, end: datetime) -> float:
    """kWh between two instants, prorating partial quarter hours."""
    total = 0.0
    for s in curve:
        s_end = s.start + QUARTER
        overlap = (min(s_end, end) - max(s.start, start)).total_seconds()
        if overlap > 0:
            total += s.kw * overlap / 3600
    return total


def day_energy(curve: Iterable[Slot], day: date, tz=None) -> float:
    return sum(s.kw * 0.25 for s in day_slots(curve, day, tz))


def power_at(curve: Iterable[Slot], when: datetime) -> float:
    """kW of the quarter hour containing `when` (0 outside the forecast)."""
    for s in curve:
        if s.start <= when < s.start + QUARTER:
            return s.kw
    return 0.0


def peak(curve: Iterable[Slot], day: date, tz=None) -> Slot | None:
    slots = day_slots(curve, day, tz)
    if not slots:
        return None
    best = max(slots, key=lambda s: s.kw)
    return best if best.kw > 0 else None


def covers_day(curve: Sequence[Slot], day: date, tz=None) -> bool:
    """True when the curve has data for the whole daylight part of `day`."""
    hours = {s.start.astimezone(tz).hour if tz else s.start.hour for s in day_slots(curve, day, tz)}
    return set(range(6, 21)) <= hours


def half_hours(curve: Iterable[Slot], day: date, tz=None) -> list[dict]:
    """Solcast-format list: period_start (ISO, local) and pv_estimate (kW)."""
    buckets: dict[datetime, list[float]] = {}
    for s in day_slots(curve, day, tz):
        local = s.start.astimezone(tz) if tz else s.start
        key = local - timedelta(minutes=local.minute % 30, seconds=local.second)
        buckets.setdefault(key, []).append(s.kw)
    return [{"period_start": k.isoformat(), "pv_estimate": round(sum(v) / len(v), 4)}
            for k, v in sorted(buckets.items())]


def quarter_hours(curve: Iterable[Slot], day: date, tz=None) -> list[dict]:
    return [{"period_start": (s.start.astimezone(tz) if tz else s.start).isoformat(), "pv_estimate": s.kw}
            for s in day_slots(curve, day, tz)]


def hourly_wh(curve: Iterable[Slot]) -> dict[datetime, float]:
    """Energy per clock hour (Wh), keyed by the start of the hour (UTC)."""
    out: dict[datetime, float] = {}
    for s in curve:
        utc = s.start.astimezone(timezone.utc)
        hour = utc.replace(minute=0, second=0, microsecond=0)
        out[hour] = out.get(hour, 0.0) + s.kw * 250
    return {h: round(v, 1) for h, v in sorted(out.items())}


@dataclass(frozen=True)
class Window:
    """Best run of consecutive quarter hours."""

    start: datetime
    end: datetime
    usable_kwh: float  # solar energy the load can use (capped, minus base load)
    solar_kwh: float  # total forecast PV energy in the window


def best_window(curve: Sequence[Slot], duration: timedelta, earliest: datetime, latest: datetime,
                power_kw: float | None = None, base_load_kw: float = 0.0) -> Window | None:
    """Window of `duration` (rounded up to quarter hours) between `earliest`
    and `latest` with the most usable solar energy.

    Usable energy per quarter hour = min(forecast - base load, load power),
    never negative; without `power_kw`, the whole surplus counts. The
    earliest window wins a tie. Returns None when no window fits or none has
    any usable energy.
    """
    steps = max(1, -(-int(duration.total_seconds()) // int(QUARTER.total_seconds())))
    slots = sorted(curve, key=lambda s: s.start)
    best: Window | None = None
    best_usable = 0.0
    for i in range(len(slots) - steps + 1):
        run = slots[i:i + steps]
        start, end = run[0].start, run[-1].start + QUARTER
        if start < earliest or end > latest:
            continue
        if any(b.start - a.start != QUARTER for a, b in zip(run, run[1:])):
            continue  # gap in the forecast
        usable = 0.0
        for s in run:
            surplus = max(s.kw - base_load_kw, 0.0)
            usable += (min(surplus, power_kw) if power_kw else surplus) * 0.25
        if usable > best_usable + 1e-9:
            best_usable = usable
            best = Window(start, end, round(usable, 3), round(sum(s.kw for s in run) * 0.25, 3))
    return best


@dataclass(frozen=True)
class AutoCorrection:
    """Hourly correction factors learned from actual production."""

    factors: tuple[float, ...]  # 24 values by UTC hour (sun position), applied instead of the manual shading
    hours: dict[int, dict]  # per UTC hour: days, median ratio, weight
    days: int  # days with at least one valid hour


# Learning parameters, shared by the integration and the tests.
AUTO_MIN_DAYS = 3  # below this, an hour keeps its manual factor
AUTO_PRIOR_DAYS = 7  # weight of the manual factor, in days (half way after 7 days)
AUTO_RANGE = (0.3, 1.2)
AUTO_MIN_SHARE = 0.05  # ignore hours forecast below 5 % of the peak power


def auto_correction(forecast_kwh: dict[datetime, float], actual_kwh: dict[datetime, float],
                    prior: Sequence[float], kwp: float, tz) -> AutoCorrection:
    """Correction factor per UTC hour from hourly forecast and actual energy.

    Shading follows the sun, not the clock: hours are grouped in UTC so that a
    daylight saving time change inside the window does not mix two sun
    positions. `prior` holds the manual factors by local hour; they are mapped
    to UTC hours with the offset of the most recent day.

    `forecast_kwh` is the archived forecast without shading, `actual_kwh` the
    measured energy, both keyed by the start of the hour. For each hour, the
    factor moves from the manual one towards the median of the
    daily ratios actual / forecast, by a weight that grows with the number of
    valid days: w = days / (days + AUTO_PRIOR_DAYS), and 0 below AUTO_MIN_DAYS.
    The median ignores a few odd days (inverter offline, unexpected sun).
    """
    ratios: dict[int, list[float]] = {h: [] for h in range(24)}
    valid_days: set[date] = set()
    by_day: dict[date, list[tuple[int, float, float]]] = {}
    for hour, forecast in forecast_kwh.items():
        actual = actual_kwh.get(hour)
        if actual is None or forecast < AUTO_MIN_SHARE * kwp:
            continue
        by_day.setdefault(hour.astimezone(tz).date(), []).append((hour.astimezone(timezone.utc).hour, forecast, actual))
    for day, rows in by_day.items():
        # A day producing almost nothing while sun was forecast is an outage, not shading.
        if sum(a for _, _, a in rows) < 0.05 * sum(f for _, f, _ in rows):
            continue
        valid_days.add(day)
        for h, forecast, actual in rows:
            ratios[h].append(min(max(actual / forecast, 0.0), 2.0))
    reference = max(forecast_kwh) if forecast_kwh else datetime.now(timezone.utc)
    factors = list(manual_by_utc_hour(prior, reference.date(), tz))
    prior_utc = tuple(factors)
    hours: dict[int, dict] = {}
    for h, values in ratios.items():
        if not values:
            continue
        values.sort()
        n = len(values)
        median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
        weight = n / (n + AUTO_PRIOR_DAYS) if n >= AUTO_MIN_DAYS else 0.0
        if weight:
            value = prior_utc[h] + (median - prior_utc[h]) * weight
            factors[h] = round(min(max(value, AUTO_RANGE[0]), AUTO_RANGE[1]), 3)
        hours[h] = {"days": n, "median_ratio": round(median, 3), "weight": round(weight, 2)}
    return AutoCorrection(tuple(factors), hours, len(valid_days))


def local_hour_of_utc(hour: int, day: date, tz) -> int:
    """Local clock hour of a UTC hour on a given day."""
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).astimezone(tz).hour


def manual_by_utc_hour(manual: Sequence[float], day: date, tz) -> tuple[float, ...]:
    """Manual factors (by local hour) re-indexed by UTC hour, with the offset of `day`."""
    return tuple(manual[local_hour_of_utc(h, day, tz)] for h in range(24))


def parse_shading(text: str) -> tuple[float, ...]:
    """'8=0.7, 17=0.5' -> 24 factors. Raises ValueError on bad input."""
    factors = [1.0] * 24
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        hour, _, value = part.partition("=")
        h, f = int(hour), float(value.replace(" ", ""))
        if not 0 <= h <= 23 or not 0 <= f <= 1.5:
            raise ValueError(part)
        factors[h] = f
    return tuple(factors)


def format_shading(factors: Sequence[float]) -> str:
    return ", ".join(f"{h}={f:g}" for h, f in enumerate(factors) if f != 1.0)
