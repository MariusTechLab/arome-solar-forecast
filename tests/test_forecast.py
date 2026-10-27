"""Tests of the integration's forecast module (no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from test_templates import fixture, model  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "arome_forecast", ROOT / "custom_components" / "arome_solar_forecast" / "forecast.py")
fc = importlib.util.module_from_spec(_spec)
sys.modules["arome_forecast"] = fc
_spec.loader.exec_module(fc)

PARIS = ZoneInfo("Europe/Paris")


def response(data: dict) -> dict:
    return {"minutely_15": data}


class CurveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = fixture(1100)
        shading = [1.0] * 24
        shading[17] = 0.5
        self.system = fc.PvSystem(arrays=(fc.PvArray(3, 30, 180),), inverter_kw=2.5, shading=tuple(shading))
        self.curve = fc.compute_curve(self.system, [response(self.data)], PARIS)
        self.expected = model(self.data, 3, 2.5, shading)

    def test_same_as_package_formula(self) -> None:
        self.assertEqual(len(self.curve), 192)
        for slot, (start, p) in zip(self.curve, self.expected):
            self.assertEqual(slot.start, start)
            self.assertAlmostEqual(slot.kw, p, places=3)
        self.assertEqual(self.curve[0].start.tzinfo, PARIS)

    def test_day_energy_and_half_hours(self) -> None:
        day = date(2026, 6, 21)
        energy = fc.day_energy(self.curve, day, PARIS)
        expected = sum(p * 0.25 for s, p in self.expected if s.astimezone(PARIS).date() == day)
        self.assertAlmostEqual(energy, expected, places=3)
        half = fc.half_hours(self.curve, day, PARIS)
        self.assertAlmostEqual(sum(x["pv_estimate"] * 0.5 for x in half), energy, delta=0.01)
        self.assertTrue(all(x["period_start"][14:16] in ("00", "30") for x in half))
        json.dumps(half)  # serializable for attributes and service responses

    def test_energy_between_prorates(self) -> None:
        start = datetime(2026, 6, 21, 12, 5, tzinfo=PARIS)
        end = start + timedelta(minutes=10)
        slot = next(s for s in self.curve if s.start <= start < s.start + fc.QUARTER)
        self.assertAlmostEqual(fc.energy_between(self.curve, start, end), slot.kw * 10 / 60, places=6)

    def test_power_peak_and_coverage(self) -> None:
        now = datetime(2026, 6, 21, 14, 10, tzinfo=PARIS)
        self.assertEqual(fc.power_at(self.curve, now), next(p for s, p in self.expected if s <= now < s + fc.QUARTER))
        self.assertEqual(fc.power_at(self.curve, now + timedelta(days=5)), 0.0)
        top = fc.peak(self.curve, date(2026, 6, 21), PARIS)
        self.assertAlmostEqual(top.kw, 2.5)
        self.assertTrue(fc.covers_day(self.curve, date(2026, 6, 21), PARIS))
        self.assertFalse(fc.covers_day(self.curve, date(2026, 6, 23), PARIS))

    def test_hourly_wh_for_energy_dashboard(self) -> None:
        wh = fc.hourly_wh(self.curve)
        self.assertAlmostEqual(sum(wh.values()) / 1000, sum(s.kw * 0.25 for s in self.curve), places=2)
        self.assertTrue(all(h.minute == 0 and h.tzinfo == timezone.utc for h in wh))

    def test_missing_values_and_two_planes(self) -> None:
        east, west = fixture(800), fixture(600)
        east["global_tilted_irradiance"][50] = None
        system = fc.PvSystem(arrays=(fc.PvArray(2, 20, 90), fc.PvArray(1.5, 20, 270)))
        curve = fc.compute_curve(system, [response(east), response(west)])
        e, w = model(fixture(800), 2, None), model(west, 1.5, None)
        self.assertAlmostEqual(curve[50].kw, round(w[50][1], 4), places=4)
        self.assertAlmostEqual(curve[60].kw, e[60][1] + w[60][1], places=3)
        with self.assertRaises(ValueError):
            fc.compute_curve(system, [response(east)])

    def test_has_irradiance(self) -> None:
        self.assertTrue(fc.has_irradiance(response(self.data)))
        self.assertFalse(fc.has_irradiance({"minutely_15": {"time": ["x"], "global_tilted_irradiance": [None]}}))
        self.assertFalse(fc.has_irradiance({}))


class BestWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        system = fc.PvSystem(arrays=(fc.PvArray(3, 30, 180),), inverter_kw=2.5)
        self.curve = fc.compute_curve(system, [response(fixture(1100))], PARIS)
        self.day_start = datetime(2026, 6, 21, 0, 0, tzinfo=PARIS)
        self.day_end = self.day_start + timedelta(days=1)

    def brute_force(self, minutes: int, earliest, latest, power=None, base=0.0):
        steps = -(-minutes // 15)
        best = None
        for i in range(len(self.curve) - steps + 1):
            run = self.curve[i:i + steps]
            if run[0].start < earliest or run[-1].start + fc.QUARTER > latest:
                continue
            usable = sum((min(max(s.kw - base, 0), power) if power else max(s.kw - base, 0)) * 0.25 for s in run)
            if best is None or usable > best[1] + 1e-9:
                best = (run[0].start, usable)
        return best

    def test_75_minutes_matches_brute_force(self) -> None:
        w = fc.best_window(self.curve, timedelta(minutes=75), self.day_start, self.day_end)
        start, usable = self.brute_force(75, self.day_start, self.day_end)
        self.assertEqual(w.start, start)
        self.assertEqual(w.end - w.start, timedelta(minutes=75))
        self.assertAlmostEqual(w.usable_kwh, usable, places=3)
        self.assertAlmostEqual(w.solar_kwh, w.usable_kwh, places=3)  # no cap, no base load

    def test_power_cap_and_base_load(self) -> None:
        w = fc.best_window(self.curve, timedelta(minutes=60), self.day_start, self.day_end,
                           power_kw=1.0, base_load_kw=0.3)
        start, usable = self.brute_force(60, self.day_start, self.day_end, power=1.0, base=0.3)
        self.assertEqual(w.start, start)  # earliest of the windows that reach the cap
        self.assertAlmostEqual(w.usable_kwh, 1.0, places=3)
        self.assertGreater(w.solar_kwh, w.usable_kwh)

    def test_bounds_rounding_and_no_window(self) -> None:
        after = datetime(2026, 6, 21, 15, 0, tzinfo=PARIS)
        before = datetime(2026, 6, 21, 18, 0, tzinfo=PARIS)
        w = fc.best_window(self.curve, timedelta(minutes=70), after, before)
        self.assertGreaterEqual(w.start, after)
        self.assertLessEqual(w.end, before)
        self.assertEqual(w.end - w.start, timedelta(minutes=75))  # 70 min rounded up
        night = datetime(2026, 6, 21, 23, 0, tzinfo=PARIS)
        self.assertIsNone(fc.best_window(self.curve, timedelta(minutes=30), night, self.day_end))
        self.assertIsNone(fc.best_window(self.curve, timedelta(hours=4), after, before))  # does not fit


class AutoCorrectionTest(unittest.TestCase):
    """Gradual learning: nothing before 3 days, then w = days / (days + 7)."""

    def history(self, days: int, ratio_by_hour: dict[int, float], outage_days: tuple[int, ...] = (),
                odd_days: dict[int, float] | None = None):
        forecast, actual = {}, {}
        for d in range(days):
            for h in range(8, 18):  # local hours, Paris summer time = UTC + 2
                start = datetime(2026, 6, 1 + d, h, tzinfo=PARIS).astimezone(timezone.utc)
                forecast[start] = 1.5
                ratio = ratio_by_hour.get(h, 1.0)
                if odd_days and d in odd_days:
                    ratio = odd_days[d]
                actual[start] = 0.0 if d in outage_days else 1.5 * ratio
        return forecast, actual

    def run_auto(self, forecast, actual, prior=None):
        return fc.auto_correction(forecast, actual, prior or (1.0,) * 24, 3.0, PARIS)

    def test_no_correction_before_three_days(self) -> None:
        r = self.run_auto(*self.history(2, {17: 0.5}))
        self.assertEqual(r.factors[17], 1.0)
        self.assertEqual(r.hours[17]["weight"], 0.0)
        self.assertEqual(r.days, 2)

    def test_gradual_weight(self) -> None:
        for days, expected in ((3, 1 - 0.5 * 3 / 10), (7, 1 - 0.5 * 7 / 14), (14, 1 - 0.5 * 14 / 21)):
            r = self.run_auto(*self.history(days, {17: 0.5}))
            self.assertAlmostEqual(r.factors[17], round(expected, 3), places=3, msg=f"{days} days")
            self.assertEqual(r.factors[12], 1.0)  # no gap at noon, no change

    def test_starts_from_manual_factor(self) -> None:
        prior = [1.0] * 24
        prior[17] = 0.4
        r = self.run_auto(*self.history(7, {17: 0.5}), prior=tuple(prior))
        self.assertAlmostEqual(r.factors[17], 0.4 + (0.5 - 0.4) * 0.5, places=3)
        r = self.run_auto(*self.history(2, {17: 0.5}), prior=tuple(prior))
        self.assertEqual(r.factors[17], 0.4)  # too few days: manual factor kept

    def test_outage_and_odd_days_are_ignored(self) -> None:
        r = self.run_auto(*self.history(10, {17: 0.5}, outage_days=(3, 4)))
        self.assertEqual(r.days, 8)
        self.assertEqual(r.hours[17]["days"], 8)
        r = self.run_auto(*self.history(10, {17: 0.5}, odd_days={1: 1.8}))
        self.assertEqual(r.hours[17]["median_ratio"], 0.5)  # one very sunny surprise does not move the median

    def test_low_forecast_hours_and_bounds(self) -> None:
        forecast, actual = self.history(14, {9: 0.05, 11: 1.9})
        dawn = datetime(2026, 6, 2, 6, tzinfo=PARIS).astimezone(timezone.utc)
        forecast[dawn], actual[dawn] = 0.1, 0.0  # under 5 % of 3 kWp: ignored
        r = self.run_auto(forecast, actual)
        self.assertNotIn(6, r.hours)
        self.assertAlmostEqual(r.factors[9], round(1 - 0.95 * 14 / 21, 3), places=3)
        self.assertEqual(r.factors[11], fc.AUTO_RANGE[1])  # 1.6 capped at 1.2

    def test_hourly_archive_curve(self) -> None:
        hourly = {"time": [f"2026-06-21T{h:02d}:00" for h in range(24)],
                  "global_tilted_irradiance": [0] * 10 + [800] * 4 + [0] * 10,
                  "temperature_2m": [20] * 24}
        system = fc.PvSystem(arrays=(fc.PvArray(3, 30, 180),))
        curve = fc.compute_curve(system, [{"hourly": hourly}], timezone.utc, block_name="hourly",
                                 step=timedelta(hours=1))
        self.assertEqual(curve[10].start, datetime(2026, 6, 21, 9, tzinfo=timezone.utc))  # value at 10:00 = hour 9-10
        self.assertGreater(curve[10].kw, 1.5)


class HelpersTest(unittest.TestCase):
    def test_azimuth_and_params(self) -> None:
        self.assertEqual(fc.PvArray(1, 18, 165).openmeteo_azimuth, -15)
        params = fc.request_params(45.76, 4.84, "meteofrance_arome_france", fc.PvArray(1, 18, 180), 2)
        self.assertEqual((params["tilt"], params["azimuth"], params["forecast_days"]), ("18", "0", "2"))

    def test_shading_parsing(self) -> None:
        factors = fc.parse_shading("8=0.7; 17 = 0.5")
        self.assertEqual((factors[8], factors[17], factors[12]), (0.7, 0.5, 1.0))
        self.assertEqual(fc.format_shading(factors), "8=0.7, 17=0.5")
        self.assertEqual(fc.parse_shading(""), (1.0,) * 24)
        for bad in ("24=0.5", "8=2", "8", "x=1"):
            with self.assertRaises(ValueError):
                fc.parse_shading(bad)


class IntegrationFilesTest(unittest.TestCase):
    BASE = ROOT / "custom_components" / "arome_solar_forecast"

    def test_translations_match(self) -> None:
        strings = json.loads((self.BASE / "strings.json").read_text(encoding="utf-8"))
        for lang in ("en", "fr"):
            data = json.loads((self.BASE / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
            self.assertEqual(self._keys(strings), self._keys(data), lang)

    def test_every_sensor_has_a_name(self) -> None:
        strings = json.loads((self.BASE / "strings.json").read_text(encoding="utf-8"))
        names = set(strings["entity"]["sensor"])
        keys = set(re_findall(r'translation_key="([a-z0-9_]+)"', (self.BASE / "sensor.py").read_text(encoding="utf-8")))
        self.assertEqual(keys, names)

    def test_every_action_field_is_translated(self) -> None:
        import yaml

        services = yaml.safe_load((self.BASE / "services.yaml").read_text(encoding="utf-8"))
        for lang_file in (self.BASE / "strings.json", self.BASE / "translations" / "fr.json"):
            texts = json.loads(lang_file.read_text(encoding="utf-8"))["services"]
            for service, spec in services.items():
                self.assertEqual(set(spec["fields"]), set(texts[service]["fields"]), f"{lang_file.name} {service}")

    def test_manifest_version_matches_const(self) -> None:
        manifest = json.loads((self.BASE / "manifest.json").read_text(encoding="utf-8"))
        const = (self.BASE / "const.py").read_text(encoding="utf-8")
        self.assertIn(f'VERSION: Final = "{manifest["version"]}"', const)

    def _keys(self, node, prefix=""):
        if isinstance(node, dict):
            return {k for key, value in node.items() for k in self._keys(value, f"{prefix}/{key}")}
        return {prefix}


def re_findall(pattern: str, text: str) -> list[str]:
    import re
    return re.findall(pattern, text)


if __name__ == "__main__":
    unittest.main()
