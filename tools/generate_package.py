#!/usr/bin/env python3
"""Generate the Home Assistant package for an AROME (Open-Meteo) PV forecast.

Standard library only. Examples:

    python tools/generate_package.py --latitude 45.76 --longitude 4.84 \
        --array kwp=3.0,tilt=30,azimuth=180 --inverter-kw 2.5

    # East/west roof, French entity names, daily comparison with Solcast
    python tools/generate_package.py --latitude 45.76 --longitude 4.84 \
        --array kwp=2.0,tilt=20,azimuth=90 --array kwp=2.0,tilt=20,azimuth=270 \
        --lang fr --actual-entity sensor.solar_energy_today \
        --compare Solcast=sensor.solcast_pv_forecast_forecast_today \
        --output packages/arome_solar_forecast.yaml

Azimuths are given as on a compass (0 = north, 90 = east, 180 = south,
270 = west) and converted to the Open-Meteo convention (0 = south,
-90 = east, 90 = west).
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

VERSION = "1.0.0"
API = "https://api.open-meteo.com/v1/forecast"

# Approximate extent of the AROME France 0.025° domain served by Open-Meteo.
AROME_DOMAIN = {"lat": (37.5, 55.4), "lon": (-12.0, 16.0)}

NAMES = {
    "en": {
        "title": "PV forecast AROME",
        "raw": "raw data",
        "curve": "curve",
        "today": "today",
        "tomorrow": "tomorrow",
        "remaining": "remaining today",
        "power_now": "power now",
        "power_1h": "power in 1 hour",
        "peak": "peak today",
        "check": "PV forecast check",
        "previous_day": "previous day",
        "morning": "morning",
        "actual": "actual production today",
        "error": "error",
    },
    "fr": {
        "title": "Prévision PV AROME",
        "raw": "données brutes",
        "curve": "courbe",
        "today": "aujourd'hui",
        "tomorrow": "demain",
        "remaining": "restante aujourd'hui",
        "power_now": "puissance maintenant",
        "power_1h": "puissance dans 1 heure",
        "peak": "pic aujourd'hui",
        "check": "Contrôle prévision PV",
        "previous_day": "veille",
        "morning": "matin",
        "actual": "production réelle du jour",
        "error": "écart",
    },
}


@dataclass
class Array:
    kwp: float
    tilt: float
    azimuth: float  # compass degrees

    @property
    def openmeteo_azimuth(self) -> float:
        """Compass azimuth (180 = south) to Open-Meteo (0 = south, -90 = east)."""
        value = (self.azimuth - 180.0) % 360.0
        return value - 360.0 if value > 180.0 else value


def slugify(text: str) -> str:
    """Approximate Home Assistant's entity_id slug for a name."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def parse_array(spec: str) -> Array:
    fields = dict(part.split("=", 1) for part in spec.split(","))
    unknown = set(fields) - {"kwp", "tilt", "azimuth"}
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown array field(s): {', '.join(sorted(unknown))}")
    try:
        array = Array(float(fields["kwp"]), float(fields.get("tilt", 30)), float(fields.get("azimuth", 180)))
    except (KeyError, ValueError) as err:
        raise argparse.ArgumentTypeError(f"invalid array '{spec}': {err}") from err
    if array.kwp <= 0 or not 0 <= array.tilt <= 90 or not 0 <= array.azimuth <= 360:
        raise argparse.ArgumentTypeError(f"out-of-range array '{spec}'")
    return array


def parse_shading(spec: str) -> list[float]:
    factors = [1.0] * 24
    if not spec:
        return factors
    for part in spec.split(","):
        hour, factor = part.split("=", 1)
        h, f = int(hour), float(factor)
        if not 0 <= h <= 23 or not 0 <= f <= 1.5:
            raise argparse.ArgumentTypeError(f"invalid shading entry '{part}'")
        factors[h] = f
    return factors


def parse_compare(spec: str) -> tuple[str, str]:
    label, _, entity = spec.partition("=")
    if not label or not entity.startswith("sensor."):
        raise argparse.ArgumentTypeError("--compare expects LABEL=sensor.entity_id")
    return label.strip(), entity.strip()


def build(args: argparse.Namespace) -> str:
    n = NAMES[args.lang]
    title = args.title or n["title"]
    prefix = slugify(title)
    arrays: list[Array] = args.array or [Array(3.0, 30.0, 180.0)]

    def sid(*parts: str) -> str:
        return "sensor." + slugify(" ".join((title,) + parts))

    raw_names = [f"{title} {n['raw']}" + (f" {i + 1}" if len(arrays) > 1 else "") for i in range(len(arrays))]
    raw_ids = ["sensor." + slugify(name) for name in raw_names]
    curve_id = sid(n["curve"])
    today_id = sid(n["today"])
    ac_limit = number(args.inverter_kw) if args.inverter_kw else "none"

    out: list[str] = []
    w = out.append
    w(f"# AROME solar forecast for Home Assistant, v{VERSION}")
    w("# https://github.com/MariusTechLab/arome-solar-forecast")
    w("# Generated by tools/generate_package.py. Weather data: Open-Meteo.com (CC BY 4.0),")
    w(f"# model {args.model}.")
    w("#")
    w("# PV system")
    for i, a in enumerate(arrays, 1):
        w(f"#   array {i}: {number(a.kwp)} kWp, tilt {number(a.tilt)} deg, azimuth {number(a.azimuth)} deg"
          f" (compass; Open-Meteo {number(a.openmeteo_azimuth)})")
    w(f"#   inverter AC limit: {ac_limit} kW, system efficiency {number(args.system_efficiency)},"
      f" inverter efficiency {number(args.inverter_efficiency)}")
    w(f"#   temperature coefficient {number(args.temp_coefficient)} /degC,"
      f" cell heating {number(args.cell_heating)} degC per W/m2")
    w("#")
    w("# Every quarter hour (irradiance G is the mean of the preceding 15 minutes):")
    w("#   cell temperature = air temperature + cell heating x G")
    w("#   DC power = kWp x G / 1000 x (1 + temperature coefficient x (cell temperature - 25))")
    w("#              x system efficiency x shading factor of the local hour")
    w("#   AC power = min(inverter AC limit, sum of DC power x inverter efficiency)")
    w("")
    w("rest:")
    for a, name in zip(arrays, raw_names):
        url = (f"{API}?latitude={number(args.latitude)}&longitude={number(args.longitude)}"
               f"&models={args.model}&minutely_15=global_tilted_irradiance,temperature_2m"
               f"&tilt={number(a.tilt)}&azimuth={number(a.openmeteo_azimuth)}"
               f"&forecast_days={args.forecast_days}&timezone=UTC")
        w("  - resource: >-")
        w(f"      {url}")
        w(f"    scan_interval: {args.scan_interval}")
        w("    timeout: 30")
        w("    sensor:")
        w(f"      - name: {name}")
        w(f"        unique_id: {slugify(name)}")
        w("        icon: mdi:weather-partly-cloudy")
        w('        value_template: "{{ value_json.minutely_15.time | count }}"')
        w('        json_attributes_path: "$.minutely_15"')
        w("        json_attributes:")
        w("          - time")
        w("          - global_tilted_irradiance")
        w("          - temperature_2m")
    w("")
    w("template:")
    w("  - sensor:")

    arrays_jinja = ", ".join(f"{{'raw': '{rid}', 'kwp': {number(a.kwp)}}}" for a, rid in zip(arrays, raw_ids))
    shading_jinja = ", ".join(number(f) for f in args.shading)
    avail_raw = " and ".join(f"state_attr('{rid}', 'time') is list" for rid in raw_ids)
    w(f"      - name: {title} {n['curve']}")
    w(f"        unique_id: {prefix}_curve")
    w("        icon: mdi:chart-bell-curve")
    w(f'        availability: "{{{{ {avail_raw} }}}}"')
    w(f"        state: \"{{{{ state_attr('{raw_ids[0]}', 'time') | count }}}}\"")
    w("        attributes:")
    w("          # Quarter-hour forecast: period_start (local time), pv_estimate (kW).")
    w("          detailedForecast15: >-")
    curve15 = f"""\
{{%- set arrays = [{arrays_jinja}] -%}}
{{%- set ac_limit = {ac_limit} -%}}
{{%- set system_efficiency = {number(args.system_efficiency)} -%}}
{{%- set inverter_efficiency = {number(args.inverter_efficiency)} -%}}
{{%- set temp_coefficient = {number(args.temp_coefficient)} -%}}
{{%- set cell_heating = {number(args.cell_heating)} -%}}
{{%- set shading = [{shading_jinja}] -%}}
{{%- set data = namespace(g=[], t=[]) -%}}
{{%- for a in arrays -%}}
  {{%- set data.g = data.g + [state_attr(a.raw, 'global_tilted_irradiance') or []] -%}}
  {{%- set data.t = data.t + [state_attr(a.raw, 'temperature_2m') or []] -%}}
{{%- endfor -%}}
{{%- set ns = namespace(l=[]) -%}}
{{%- for t in state_attr(arrays[0].raw, 'time') or [] -%}}
  {{%- set i = loop.index0 -%}}
  {{%- set start = (as_datetime(t ~ ':00+00:00') - timedelta(minutes=15)) | as_local -%}}
  {{%- set dc = namespace(p=0) -%}}
  {{%- for a in arrays -%}}
    {{%- set j = loop.index0 -%}}
    {{%- set g = (data.g[j][i] if i < data.g[j] | count else 0) | float(0) -%}}
    {{%- set ta = (data.t[j][i] if i < data.t[j] | count else 15) | float(15) -%}}
    {{%- set cell = ta + cell_heating * g -%}}
    {{%- set dc.p = dc.p + a.kwp * g / 1000 * (1 + temp_coefficient * (cell - 25)) * system_efficiency -%}}
  {{%- endfor -%}}
  {{%- set ac = dc.p * shading[start.hour] * inverter_efficiency -%}}
  {{%- set ac = [ac, ac_limit] | min if ac_limit is not none else ac -%}}
  {{%- set ns.l = ns.l + [{{'period_start': start.isoformat(), 'pv_estimate': [ac, 0] | max | round(4)}}] -%}}
{{%- endfor -%}}
{{{{ ns.l }}}}"""
    for line in curve15.splitlines():
        w("            " + line)

    fc15 = f"state_attr('{curve_id}', 'detailedForecast15')"
    # Half-hour average of the day's quarter hours, in the Solcast format.
    half_hour = f"""\
{{%- set ns = namespace(l=[], key=none, sum=0, n=0) -%}}
{{%- for x in {fc15} | selectattr('period_start', 'search', '^' ~ d) -%}}
  {{%- set s = as_datetime(x.period_start) -%}}
  {{%- set key = (s - timedelta(minutes=s.minute % 30)).isoformat() -%}}
  {{%- if key != ns.key and ns.n > 0 -%}}
    {{%- set ns.l = ns.l + [{{'period_start': ns.key, 'pv_estimate': (ns.sum / ns.n) | round(4)}}] -%}}
    {{%- set ns.sum = 0 -%}}{{%- set ns.n = 0 -%}}
  {{%- endif -%}}
  {{%- set ns.key = key -%}}{{%- set ns.sum = ns.sum + x.pv_estimate -%}}{{%- set ns.n = ns.n + 1 -%}}
{{%- endfor -%}}
{{%- if ns.n > 0 -%}}{{%- set ns.l = ns.l + [{{'period_start': ns.key, 'pv_estimate': (ns.sum / ns.n) | round(4)}}] -%}}{{%- endif -%}}
{{{{ ns.l }}}}"""
    avail_curve = f'"{{{{ {fc15} is list }}}}"'

    def day_energy(name: str, uid: str, offset_days: int, icon: str) -> None:
        day = "now().date()" if offset_days == 0 else f"(now() + timedelta(days={offset_days})).date()"
        w(f"      - name: {title} {name}")
        w(f"        unique_id: {prefix}_{uid}")
        w("        unit_of_measurement: kWh")
        w(f"        icon: {icon}")
        w(f"        availability: {avail_curve}")
        w("        state: >-")
        w(f"          {{%- set d = {day} | string -%}}")
        w(f"          {{{{ ({fc15} | selectattr('period_start', 'search', '^' ~ d)"
          f" | map(attribute='pv_estimate') | sum * 0.25) | round(2) }}}}")
        w("        attributes:")
        w("          # Half-hour forecast in the Solcast format: period_start, pv_estimate (kW).")
        w("          detailedForecast: >-")
        w(f"            {{%- set d = {day} | string -%}}")
        for line in half_hour.splitlines():
            w("            " + line)

    day_energy(n["today"], "today", 0, "mdi:solar-power-variant")
    day_energy(n["tomorrow"], "tomorrow", 1, "mdi:solar-power-variant-outline")

    w(f"      - name: {title} {n['remaining']}")
    w(f"        unique_id: {prefix}_remaining_today")
    w("        unit_of_measurement: kWh")
    w("        icon: mdi:solar-power")
    w(f"        availability: {avail_curve}")
    w("        state: >-")
    w("          {%- set nt = now() -%}")
    w("          {%- set n = namespace(e=0) -%}")
    w(f"          {{%- for x in {fc15} -%}}")
    w("            {%- set s = as_datetime(x.period_start) -%}")
    w("            {%- set e = s + timedelta(minutes=15) -%}")
    w("            {%- if s.date() == nt.date() and e > nt -%}")
    w("              {%- set n.e = n.e + x.pv_estimate * 0.25 * (e - ([s, nt] | max)).total_seconds() / 900 -%}")
    w("            {%- endif -%}")
    w("          {%- endfor -%}")
    w("          {{ n.e | round(2) }}")

    def power_at(name: str, uid: str, minutes: int) -> None:
        w(f"      - name: {title} {name}")
        w(f"        unique_id: {prefix}_{uid}")
        w("        unit_of_measurement: W")
        w("        device_class: power")
        w("        state_class: measurement")
        w(f"        availability: {avail_curve}")
        w("        state: >-")
        w("          {%- set nt = now()" + (f" + timedelta(minutes={minutes})" if minutes else "") + " -%}")
        w("          {%- set n = namespace(p=0) -%}")
        w(f"          {{%- for x in {fc15} -%}}")
        w("            {%- set s = as_datetime(x.period_start) -%}")
        w("            {%- if s <= nt < s + timedelta(minutes=15) -%}{%- set n.p = x.pv_estimate -%}{%- endif -%}")
        w("          {%- endfor -%}")
        w("          {{ (n.p * 1000) | round(0) | int }}")

    power_at(n["power_now"], "power_now", 0)
    power_at(n["power_1h"], "power_in_1_hour", 60)

    w(f"      - name: {title} {n['peak']}")
    w(f"        unique_id: {prefix}_peak_today")
    w("        unit_of_measurement: W")
    w("        device_class: power")
    w(f"        availability: {avail_curve}")
    w("        state: >-")
    w("          {%- set d = now().date() | string -%}")
    w(f"          {{%- set l = {fc15} | selectattr('period_start', 'search', '^' ~ d)"
      " | sort(attribute='pv_estimate', reverse=true) | list -%}")
    w("          {{ (l[0].pv_estimate * 1000) | round(0) | int if l else 0 }}")
    w("        attributes:")
    w("          peak_time: >-")
    w("            {%- set d = now().date() | string -%}")
    w(f"            {{%- set l = {fc15} | selectattr('period_start', 'search', '^' ~ d)"
      " | sort(attribute='pv_estimate', reverse=true) | list -%}")
    w("            {{ l[0].period_start if l and l[0].pv_estimate > 0 else none }}")

    if args.actual_entity:
        sources = [("AROME", today_id)] + list(args.compare or [])
        check = n["check"]
        w("")
        w("  # Daily forecast check. Snapshots of each forecast for today, taken")
        w(f"  # at 00:05 (previous-day forecast) and {args.morning_time[:5]} (morning forecast), and the")
        w("  # actual production and signed errors at 23:55: (forecast - actual) / actual, in %.")
        for horizon, at in ((n["previous_day"], "00:05:00"), (n["morning"], args.morning_time)):
            w("  - triggers:")
            w("      - trigger: time")
            w(f'        at: "{at}"')
            w("    sensor:")
            for label, entity in sources:
                name = f"{check} {horizon} {label}"
                w(f"      - name: {name}")
                w(f"        unique_id: {slugify(name)}")
                w("        unit_of_measurement: kWh")
                w("        state_class: measurement")
                w(f"        state: \"{{{{ states('{entity}') | float(none) }}}}\"")
        w("  - triggers:")
        w("      - trigger: time")
        w('        at: "23:55:00"')
        w("    sensor:")
        name = f"{check} {n['actual']}"
        w(f"      - name: {name}")
        w(f"        unique_id: {slugify(name)}")
        w("        unit_of_measurement: kWh")
        w("        state_class: measurement")
        w(f"        state: \"{{{{ states('{args.actual_entity}') | float(none) }}}}\"")
        for horizon in (n["previous_day"], n["morning"]):
            for label, _entity in sources:
                snap = "sensor." + slugify(f"{check} {horizon} {label}")
                name = f"{check} {n['error']} {horizon} {label}"
                w(f"      - name: {name}")
                w(f"        unique_id: {slugify(name)}")
                w('        unit_of_measurement: "%"')
                w("        state_class: measurement")
                w("        state: >-")
                w(f"          {{%- set p = states('{snap}') | float(none) -%}}")
                w(f"          {{%- set r = states('{args.actual_entity}') | float(none) -%}}")
                w("          {{ (100 * (p - r) / ([r, 0.5] | max)) | round(1) if p is not none and r is not none else none }}")
                w("        attributes:")
                w("          error_kwh: >-")
                w(f"            {{%- set p = states('{snap}') | float(none) -%}}")
                w(f"            {{%- set r = states('{args.actual_entity}') | float(none) -%}}")
                w("            {{ (p - r) | round(2) if p is not none and r is not none else none }}")
    return "\n".join(out) + "\n"


def entity_summary(args: argparse.Namespace) -> list[str]:
    n = NAMES[args.lang]
    title = args.title or n["title"]
    keys = ["today", "tomorrow", "remaining", "power_now", "power_1h", "peak", "curve"]
    return ["sensor." + slugify(f"{title} {n[k]}") for k in keys]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--latitude", type=float, required=True)
    p.add_argument("--longitude", type=float, required=True)
    p.add_argument("--array", type=parse_array, action="append",
                   help="kwp=..,tilt=..,azimuth=.. (compass, 180 = south); repeat for several roof planes")
    p.add_argument("--inverter-kw", type=float, default=None, help="inverter AC limit (clipping), kW")
    p.add_argument("--system-efficiency", type=float, default=0.98,
                   help="DC losses: soiling, wiring, mismatch (default 0.98)")
    p.add_argument("--inverter-efficiency", type=float, default=0.96)
    p.add_argument("--temp-coefficient", type=float, default=-0.004, help="Pmax coefficient per degC")
    p.add_argument("--cell-heating", type=float, default=0.03,
                   help="cell temperature rise per W/m2 (0.025 open rack, 0.035 roof-integrated)")
    p.add_argument("--shading", type=parse_shading, default=parse_shading(""),
                   help="local-hour factors, e.g. 8=0.6,16=0.5,17=0.4")
    p.add_argument("--model", default="meteofrance_arome_france",
                   help="Open-Meteo model (meteofrance_seamless for a longer horizon)")
    p.add_argument("--forecast-days", type=int, default=2, choices=range(1, 8))
    p.add_argument("--scan-interval", type=int, default=900, help="seconds between API calls")
    p.add_argument("--lang", choices=sorted(NAMES), default="en", help="entity names")
    p.add_argument("--title", help="name prefix of the entities")
    p.add_argument("--actual-entity", help="daily actual PV energy sensor (kWh, resets at midnight)")
    p.add_argument("--compare", type=parse_compare, action="append",
                   help="LABEL=sensor.other_forecast_today (needs --actual-entity)")
    p.add_argument("--morning-time", default="06:30:00", help="time of the morning snapshot")
    p.add_argument("--output", type=Path, help="write the package here instead of stdout")
    args = p.parse_args(argv)

    if args.compare and not args.actual_entity:
        p.error("--compare needs --actual-entity")
    if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", args.morning_time):
        p.error("--morning-time must be HH:MM:SS")
    if args.model.startswith("meteofrance_arome") and not (
        AROME_DOMAIN["lat"][0] <= args.latitude <= AROME_DOMAIN["lat"][1]
        and AROME_DOMAIN["lon"][0] <= args.longitude <= AROME_DOMAIN["lon"][1]
    ):
        print("warning: location outside the AROME domain; use e.g. --model best_match", file=sys.stderr)
    if args.model.startswith("meteofrance_arome") and args.forecast_days > 2:
        print("warning: AROME covers about 42 hours; use --model meteofrance_seamless for more days",
              file=sys.stderr)

    text = build(args)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {args.output}", file=sys.stderr)
        print("Main entities:", *entity_summary(args), sep="\n  ", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
