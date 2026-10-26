"""Scenario tests of the generated templates against an independent Python model.

Run: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import math
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from ha_jinja import FakeHass  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "tools" / "generate_package.py"
DAY = datetime(2026, 6, 21, tzinfo=timezone.utc)


def generate(*args: str) -> dict:
    text = subprocess.run(
        [sys.executable, str(GENERATOR), *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    return yaml.safe_load(text)


def sensors(package: dict) -> dict[str, dict]:
    return {s["unique_id"]: s for block in package["template"] for s in block["sensor"]}


def fixture(peak: float, temperature: float = 20.0) -> dict:
    """Two UTC days of quarter-hour data: a sine bell between 05:00 and 19:00 UTC."""
    times, gti, temp = [], [], []
    for k in range(2 * 96):
        t = DAY + timedelta(minutes=15 * k)
        hours = t.hour + t.minute / 60
        g = peak * math.sin(math.pi * (hours - 5) / 14) if 5 < hours < 19 else 0.0
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        gti.append(round(g, 1))
        temp.append(temperature)
    return {"time": times, "global_tilted_irradiance": gti, "temperature_2m": temp}


def model(data: dict, kwp: float, ac: float | None, shading: list[float] | None = None,
          eff: float = 0.98, inv: float = 0.96, gamma: float = -0.004, heat: float = 0.03,
          tz_offset: int = 2) -> list[tuple[datetime, float]]:
    """Independent implementation of the documented formula (local time = UTC + tz_offset)."""
    out = []
    for t, g, ta in zip(data["time"], data["global_tilted_irradiance"], data["temperature_2m"]):
        start = datetime.fromisoformat(t + ":00+00:00") - timedelta(minutes=15)
        local = start + timedelta(hours=tz_offset)
        dc = kwp * g / 1000 * (1 + gamma * (ta + heat * g - 25)) * eff
        p = dc * (shading[local.hour] if shading else 1) * inv
        if ac is not None:
            p = min(p, ac)
        out.append((start, max(p, 0)))
    return out


class GeneratedPackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pkg = generate("--latitude", "45.76", "--longitude", "4.84",
                            "--array", "kwp=3,tilt=30,azimuth=180", "--inverter-kw", "2.5",
                            "--shading", "17=0.5")
        self.s = sensors(self.pkg)
        self.hass = FakeHass(datetime(2026, 6, 21, 12, 10, tzinfo=timezone.utc))  # 14:10 local
        self.data = fixture(peak=1100)
        self.hass.set("sensor.pv_forecast_arome_raw_data", 192, self.data)
        curve = self.hass.render(self.s["pv_forecast_arome_curve"]["attributes"]["detailedForecast15"])
        self.curve = curve
        self.hass.set("sensor.pv_forecast_arome_curve", len(curve), {"detailedForecast15": curve})
        shading = [1.0] * 24
        shading[17] = 0.5
        self.expected = model(self.data, 3, 2.5, shading)

    def test_rest_request(self) -> None:
        url = self.pkg["rest"][0]["resource"]
        self.assertIn("models=meteofrance_arome_france", url)
        self.assertIn("tilt=30&azimuth=0", url)  # compass 180 = Open-Meteo 0
        self.assertIn("minutely_15=global_tilted_irradiance,temperature_2m", url)

    def test_curve_matches_model(self) -> None:
        self.assertEqual(len(self.curve), 192)
        for got, (start, p) in zip(self.curve, self.expected):
            self.assertEqual(datetime.fromisoformat(got["period_start"]), start)
            self.assertAlmostEqual(got["pv_estimate"], p, places=3)

    def test_clipping_and_night(self) -> None:
        values = [x["pv_estimate"] for x in self.curve]
        self.assertAlmostEqual(max(values), 2.5, places=4)
        self.assertEqual(values[0], 0)

    def test_shading_factor_applied_at_local_hour(self) -> None:
        unclipped = dict(model(self.data, 3, None))
        local17 = [x for x in self.curve if x["period_start"][11:13] == "17"]
        self.assertEqual(len(local17), 8)  # two days
        for x in local17:
            start = datetime.fromisoformat(x["period_start"]).astimezone(timezone.utc)
            self.assertAlmostEqual(x["pv_estimate"], min(unclipped[start] * 0.5, 2.5), places=3)

    def local_day_energy(self, day: str) -> float:
        return sum(p * 0.25 for s, p in self.expected if (s + timedelta(hours=2)).date().isoformat() == day)

    def test_today_and_tomorrow(self) -> None:
        today = self.hass.render(self.s["pv_forecast_arome_today"]["state"])
        self.assertAlmostEqual(today, round(self.local_day_energy("2026-06-21"), 2), places=2)
        tomorrow = self.hass.render(self.s["pv_forecast_arome_tomorrow"]["state"])
        self.assertAlmostEqual(tomorrow, round(self.local_day_energy("2026-06-22"), 2), places=2)
        self.assertGreater(today, 10)

    def test_solcast_format_half_hours(self) -> None:
        half = self.hass.render(self.s["pv_forecast_arome_today"]["attributes"]["detailedForecast"])
        self.assertTrue(all(x["period_start"][14:16] in ("00", "30") for x in half))
        energy = sum(x["pv_estimate"] * 0.5 for x in half)
        self.assertAlmostEqual(energy, self.local_day_energy("2026-06-21"), delta=0.01)
        self.assertEqual(set(half[0]), {"period_start", "pv_estimate"})

    def test_remaining_today(self) -> None:
        now = self.hass.now
        remaining = 0.0
        for s, p in self.expected:
            e = s + timedelta(minutes=15)
            if (s + timedelta(hours=2)).date() == now.date() and e > now:
                remaining += p * 0.25 * (e - max(s, now)).total_seconds() / 900
        got = self.hass.render(self.s["pv_forecast_arome_remaining_today"]["state"])
        self.assertAlmostEqual(got, round(remaining, 2), places=2)

    def test_power_now_and_in_one_hour(self) -> None:
        def at(t: datetime) -> float:
            return next(p for s, p in self.expected if s <= t < s + timedelta(minutes=15))

        now = self.hass.render(self.s["pv_forecast_arome_power_now"]["state"])
        later = self.hass.render(self.s["pv_forecast_arome_power_in_1_hour"]["state"])
        self.assertEqual(now, round(at(self.hass.now) * 1000))
        self.assertEqual(later, round(at(self.hass.now + timedelta(hours=1)) * 1000))

    def test_peak(self) -> None:
        peak = self.hass.render(self.s["pv_forecast_arome_peak_today"]["state"])
        self.assertEqual(peak, 2500)
        time = self.hass.render(self.s["pv_forecast_arome_peak_today"]["attributes"]["peak_time"])
        self.assertTrue(str(time).startswith("2026-06-21T"))

    def test_unavailable_without_data(self) -> None:
        hass = FakeHass(self.hass.now)
        self.assertFalse(hass.render(self.s["pv_forecast_arome_curve"]["availability"]))
        self.assertFalse(hass.render(self.s["pv_forecast_arome_today"]["availability"]))


class BestWindowExampleTest(unittest.TestCase):
    def test_best_start(self) -> None:
        example = yaml.safe_load((ROOT / "examples" / "best_window.yaml").read_text(encoding="utf-8"))
        sensor = example["template"][0]["sensor"][0]
        hass = FakeHass(datetime(2026, 6, 21, 5, 0, tzinfo=timezone.utc))  # 07:00 local
        curve = [{"period_start": s.astimezone(hass.tz).isoformat(), "pv_estimate": round(p, 4)}
                 for s, p in model(fixture(1100), 3, 2.5)]
        hass.set("sensor.pv_forecast_arome_curve", len(curve), {"detailedForecast15": curve})
        start = datetime.fromisoformat(hass.render(sensor["state"]))
        # The earliest hour where (forecast - 0.3 kW) covers most of the 2 kW appliance.
        candidates = []
        for i in range(len(curve) - 3):
            s = datetime.fromisoformat(curve[i]["period_start"])
            if s.date() != hass.now.date() or s < hass.now.replace(hour=8, minute=0):
                continue
            kwh = sum(min(max(x["pv_estimate"] - 0.3, 0), 2.0) * 0.25 for x in curve[i:i + 4])
            candidates.append((s, kwh))
        expected, best = None, -1.0
        for s, kwh in candidates:  # same rule: replace only if better by more than 5 % + 10 Wh
            if best < 0 or kwh > best * 1.05 + 0.01:
                expected, best = s, kwh
        self.assertEqual(start, expected)
        self.assertGreaterEqual(dict(candidates)[start], max(k for _, k in candidates) / 1.1)
        hass.set("sensor.pv_forecast_arome_best_start_today", start.isoformat())
        self.assertAlmostEqual(hass.render(sensor["attributes"]["solar_kwh"]), round(dict(candidates)[start], 2))


class MultiArrayAndCheckTest(unittest.TestCase):
    def test_east_west_sum_and_check(self) -> None:
        pkg = generate("--latitude", "45.76", "--longitude", "4.84",
                       "--array", "kwp=2,tilt=20,azimuth=90", "--array", "kwp=1.5,tilt=20,azimuth=270",
                       "--actual-entity", "sensor.solar_energy_today",
                       "--compare", "Other=sensor.other_forecast_today")
        urls = [r["resource"] for r in pkg["rest"]]
        self.assertIn("azimuth=-90", urls[0])
        self.assertIn("azimuth=90", urls[1])
        s = sensors(pkg)
        hass = FakeHass(datetime(2026, 6, 21, 21, 55, tzinfo=timezone.utc))
        east, west = fixture(800), fixture(600)
        hass.set("sensor.pv_forecast_arome_raw_data_1", 192, east)
        hass.set("sensor.pv_forecast_arome_raw_data_2", 192, west)
        curve = hass.render(s["pv_forecast_arome_curve"]["attributes"]["detailedForecast15"])
        e, w = model(east, 2, None), model(west, 1.5, None)
        for got, (_, pe), (_, pw) in zip(curve, e, w):
            self.assertAlmostEqual(got["pv_estimate"], pe + pw, places=3)

        hass.set("sensor.pv_forecast_check_morning_arome", 12.0)
        hass.set("sensor.solar_energy_today", 10.0)
        err = s["pv_forecast_check_error_morning_arome"]
        self.assertEqual(hass.render(err["state"]), 20.0)
        self.assertEqual(hass.render(err["attributes"]["error_kwh"]), 2.0)
        hass.set("sensor.solar_energy_today", "unavailable")
        self.assertIsNone(hass.render(err["state"]))
        self.assertIn("pv_forecast_check_previous_day_other", s)


class GeneratorTest(unittest.TestCase):
    def test_committed_package_is_up_to_date(self) -> None:
        committed = (ROOT / "packages" / "arome_solar_forecast.yaml").read_text(encoding="utf-8")
        fresh = subprocess.run(
            [sys.executable, str(GENERATOR), "--latitude", "45.76", "--longitude", "4.84",
             "--array", "kwp=3.0,tilt=30,azimuth=180", "--inverter-kw", "2.5"],
            check=True, capture_output=True, text=True, encoding="utf-8").stdout
        self.assertEqual(committed, fresh, "regenerate packages/arome_solar_forecast.yaml")

    def test_committed_example_is_up_to_date(self) -> None:
        committed = (ROOT / "examples" / "east_west_fr_with_check.yaml").read_text(encoding="utf-8")
        fresh = subprocess.run(
            [sys.executable, str(GENERATOR), "--latitude", "45.76", "--longitude", "4.84",
             "--array", "kwp=2.0,tilt=20,azimuth=90", "--array", "kwp=2.0,tilt=20,azimuth=270",
             "--inverter-kw", "3.0", "--lang", "fr", "--shading", "8=0.7,17=0.5",
             "--actual-entity", "sensor.solar_energy_today",
             "--compare", "Solcast=sensor.solcast_pv_forecast_forecast_today"],
            check=True, capture_output=True, text=True, encoding="utf-8").stdout
        self.assertEqual(committed, fresh, "regenerate examples/east_west_fr_with_check.yaml")

    def test_azimuth_conversion(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        from generate_package import Array

        for compass, om in ((180, 0), (90, -90), (270, 90), (165, -15), (195, 15), (0, 180)):
            self.assertEqual(Array(1, 30, compass).openmeteo_azimuth, om)

    def test_rejects_bad_input(self) -> None:
        result = subprocess.run([sys.executable, str(GENERATOR), "--latitude", "45", "--longitude", "4",
                                 "--array", "kwp=-1"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
