#!/usr/bin/env python3
"""Benchmark archived weather-model forecasts against your own PV production.

Reads a Home Assistant history export (CSV with entity_id,state,last_changed)
of a PV power sensor, downloads the forecasts that Open-Meteo archived for
your location (Previous Runs API), converts irradiance to PV power with the
same model as the package, and reports, for each weather model:

  * the previous-day forecast ("day-1": issued about 24 hours ahead);
  * the latest forecast ("day0": most recent run, closer to a nowcast).

One scale factor per model is fitted on the first part of the period and the
errors are measured on the rest, so a constant bias (shading, soiling,
wrong kWp) does not hide which model follows the weather best.

Standard library only (plus tzdata on Windows). Example:

    python tools/benchmark.py --csv history.csv \
        --entity sensor.pv_power --latitude 45.76 --longitude 4.84 \
        --array kwp=3,tilt=30,azimuth=180 --inverter-kw 2.5

Optional reference: another forecast recorded in Home Assistant as a power
sensor (W or kW), e.g. --reference Solcast=sensor.solcast_pv_forecast_power_now
(it is compared with the "day0" forecasts, as it is also updated during the day).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from generate_package import Array, parse_array  # noqa: E402

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
DEFAULT_MODELS = [
    "meteofrance_arome_france",
    "meteofrance_arpege_europe",
    "icon_d2",
    "icon_eu",
    "ecmwf_ifs",
    "gfs_seamless",
    "ukmo_global_deterministic_10km",
    "best_match",
]


def read_history(path: Path, entity: str) -> list[tuple[datetime, float]]:
    """State changes of one entity from a Home Assistant history CSV export."""
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("entity_id") != entity:
                continue
            try:
                value = float(row["state"])
            except ValueError:
                value = math.nan  # unavailable / unknown: a gap
            when = datetime.fromisoformat(row["last_changed"].replace("Z", "+00:00"))
            rows.append((when, value))
    rows.sort()
    if not rows:
        raise SystemExit(f"no rows for {entity} in {path}")
    finite = [v for _, v in rows if not math.isnan(v)]
    if finite and max(finite) > 50:  # watts
        rows = [(t, v / 1000) for t, v in rows]
    return rows


def hourly_means(rows: list[tuple[datetime, float]], max_gap: timedelta) -> dict[datetime, float]:
    """Time-weighted hourly mean (kW) of a step series; hours with gaps are dropped."""
    acc: dict[datetime, list[float]] = defaultdict(lambda: [0.0, 0.0])  # energy, covered seconds
    for (t0, v), (t1, _) in zip(rows, rows[1:]):
        if math.isnan(v) or t1 - t0 > max_gap:
            continue
        t = t0
        while t < t1:
            hour = t.replace(minute=0, second=0, microsecond=0)
            end = min(t1, hour + timedelta(hours=1))
            seconds = (end - t).total_seconds()
            acc[hour][0] += max(v, 0) * seconds
            acc[hour][1] += seconds
            t = end
    return {h: e / s for h, (e, s) in acc.items() if s >= 0.8 * 3600}


def fetch(model: str, lat: float, lon: float, array: Array, start: date, end: date) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "models": model, "timezone": "UTC",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "tilt": array.tilt, "azimuth": array.openmeteo_azimuth,
        "hourly": "global_tilted_irradiance,global_tilted_irradiance_previous_day1,"
                  "temperature_2m,temperature_2m_previous_day1",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as response:
        data = json.load(response)
    if data.get("error"):
        raise RuntimeError(data.get("reason", "API error"))
    return data["hourly"]


def pv_power(g: float | None, ta: float | None, kwp: float, args: argparse.Namespace) -> float | None:
    if g is None:
        return None
    ta = 15.0 if ta is None else ta
    cell = ta + args.cell_heating * g
    return kwp * g / 1000 * (1 + args.temp_coefficient * (cell - 25)) * args.system_efficiency


def model_series(args: argparse.Namespace, model: str, start: date, end: date) -> dict[str, dict[datetime, float]]:
    """Hourly AC power (kW) for day0 and day-1, keyed by the start of the hour (UTC)."""
    out: dict[str, dict[datetime, float]] = {"day0": {}, "day-1": {}}
    for array in args.array:
        h = fetch(model, args.latitude, args.longitude, array, start, end)
        for i, t in enumerate(h["time"]):
            # Open-Meteo radiation is the mean of the preceding hour.
            hour = datetime.fromisoformat(t + ":00+00:00") - timedelta(hours=1)
            for key, gk, tk in (("day0", "global_tilted_irradiance", "temperature_2m"),
                                ("day-1", "global_tilted_irradiance_previous_day1", "temperature_2m_previous_day1")):
                p = pv_power(h[gk][i], h[tk][i], array.kwp, args)
                if p is None:
                    continue
                out[key][hour] = out[key].get(hour, 0.0) + p
    for key in out:
        for hour, dc in out[key].items():
            ac = dc * args.inverter_efficiency
            out[key][hour] = min(ac, args.inverter_kw) if args.inverter_kw else ac
    return out


def evaluate(pred: dict[datetime, float], actual: dict[datetime, float], tz: ZoneInfo,
             split: datetime, complete_days: set[date]) -> dict | None:
    common = [h for h in actual if h in pred]
    train = [h for h in common if h < split]
    test = [h for h in common if h >= split]
    sxx = sum(pred[h] ** 2 for h in train)
    if not test or sxx == 0:
        return None
    k = sum(pred[h] * actual[h] for h in train) / sxx
    hourly = [abs(k * pred[h] - actual[h]) for h in test if pred[h] > 0.01 or actual[h] > 0.01]
    days_p: dict[date, float] = defaultdict(float)
    days_a: dict[date, float] = defaultdict(float)
    days_raw: dict[date, float] = defaultdict(float)
    for h in test:
        d = h.astimezone(tz).date()
        if d in complete_days:
            days_p[d] += k * pred[h]
            days_raw[d] += pred[h]
            days_a[d] += actual[h]
    if not days_a:
        return None
    total = sum(days_a.values())
    mae = sum(abs(days_p[d] - days_a[d]) for d in days_a) / len(days_a)
    bias_raw = 100 * (sum(days_raw.values()) - total) / total
    return {
        "scale": round(k, 3),
        "daily_error_kwh": round(mae, 2),
        "daily_error_pct": round(100 * mae / (total / len(days_a)), 1),
        "hourly_mae_kw": round(sum(hourly) / len(hourly), 3),
        "raw_bias_pct": round(bias_raw, 1),
        "days": len(days_a),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", type=Path, required=True, help="Home Assistant history export")
    p.add_argument("--entity", required=True, help="actual PV power sensor (W or kW)")
    p.add_argument("--latitude", type=float, required=True)
    p.add_argument("--longitude", type=float, required=True)
    p.add_argument("--array", type=parse_array, action="append", required=True)
    p.add_argument("--inverter-kw", type=float)
    p.add_argument("--system-efficiency", type=float, default=0.98)
    p.add_argument("--inverter-efficiency", type=float, default=0.96)
    p.add_argument("--temp-coefficient", type=float, default=-0.004)
    p.add_argument("--cell-heating", type=float, default=0.03)
    p.add_argument("--models", default=",".join(DEFAULT_MODELS), help="comma-separated Open-Meteo models")
    p.add_argument("--reference", action="append", default=[],
                   help="LABEL=sensor.entity: another forecast's power sensor in the same CSV")
    p.add_argument("--train-fraction", type=float, default=0.5, help="share of days used to fit the scale")
    p.add_argument("--timezone", default="Europe/Paris")
    p.add_argument("--json", action="store_true", help="print JSON instead of a Markdown table")
    args = p.parse_args(argv)
    tz = ZoneInfo(args.timezone)

    gap = timedelta(minutes=30)
    actual = hourly_means(read_history(args.csv, args.entity), gap)
    if not actual:
        raise SystemExit("not enough data in the CSV")
    first, last = min(actual), max(actual)
    start, end = first.date(), min(last.date(), datetime.now(timezone.utc).date() - timedelta(days=1))
    by_day: dict[date, int] = defaultdict(int)
    for h in actual:
        local = h.astimezone(tz)
        if 8 <= local.hour <= 16:
            by_day[local.date()] += 1
    complete = {d for d, n in by_day.items() if n >= 9}
    days = sorted(complete)
    if len(days) < 4:
        raise SystemExit(f"only {len(days)} complete days; at least 4 are needed")
    split_day = days[max(1, int(len(days) * args.train_fraction)) - 1] + timedelta(days=1)
    split = datetime.combine(split_day, datetime.min.time(), tz).astimezone(timezone.utc)

    results = []
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        try:
            series = model_series(args, model, start, end)
        except Exception as err:  # noqa: BLE001 - report and continue with other models
            print(f"{model}: {err}", file=sys.stderr)
            continue
        for horizon in ("day-1", "day0"):
            r = evaluate(series[horizon], actual, tz, split, complete)
            if r:
                results.append({"source": model, "horizon": horizon, **r})
    for spec in args.reference:
        label, _, entity = spec.partition("=")
        ref = hourly_means(read_history(args.csv, entity), gap)
        r = evaluate(ref, actual, tz, split, complete)
        if r:
            results.append({"source": label, "horizon": "intraday", **r})

    results.sort(key=lambda r: (r["horizon"] != "day-1", r["daily_error_pct"]))
    if args.json:
        print(json.dumps({"period": [start.isoformat(), end.isoformat()], "test_from": split_day.isoformat(),
                          "results": results}, indent=2))
        return 0
    test_days = len([d for d in days if d >= split_day])
    print(f"Period {start} to {end}; scale fitted before {split_day}; {test_days} complete test days.\n")
    print("| Source | Horizon | Daily error | Daily error (kWh) | Hourly MAE (kW) | Scale | Raw bias |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['source']} | {r['horizon']} | {r['daily_error_pct']} % | {r['daily_error_kwh']} "
              f"| {r['hourly_mae_kw']} | {r['scale']} | {r['raw_bias_pct']:+} % |")
    print("\nScale: factor fitted on the training days (1 = the physical model is unbiased).")
    print("Raw bias: (unscaled forecast - actual) / actual on the test days. A large positive")
    print("value usually means shading, soiling or a lower real capacity than declared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
