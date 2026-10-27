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
    shading: tuple[float, ...] = field(default_factory=lambda: (1.0,) * 24)


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


def compute_curve(system: PvSystem, responses: Sequence[dict], tz: tzinfo | None = None) -> list[Slot]:
    """AC power per quarter hour from one API response per roof plane.

    Radiation is the mean of the preceding 15 minutes, so a value stamped T
    describes the quarter hour starting at T - 15 min.
    """
    if len(responses) != len(system.arrays):
        raise ValueError("one response per roof plane is required")
    series = []
    for response in responses:
        block = response.get("minutely_15") or {}
        times = block.get("time") or []
        series.append((
            {t: g for t, g in zip(times, block.get("global_tilted_irradiance") or [])},
            {t: c for t, c in zip(times, block.get("temperature_2m") or [])},
        ))
    base_times = (responses[0].get("minutely_15") or {}).get("time") or []
    slots: list[Slot] = []
    for t in base_times:
        start = datetime.fromisoformat(t).replace(tzinfo=timezone.utc) - QUARTER
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
        ac = dc * system.shading[start.hour] * system.inverter_efficiency
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
