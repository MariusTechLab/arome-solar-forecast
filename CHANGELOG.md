# Changelog

## 1.4.0

- Automatic correction by time slots of 30 minutes (default), 15 minutes or
  1 hour, new option "Correction resolution". Below one hour it uses the
  15-minute archived forecasts and the 5-minute statistics of the production
  sensor (kept 10 days by default by the recorder).
- The sensor shows the factors by local clock time (HH:MM) and the slot length.
- Walk-forward test on a real installation in docs/benchmark.md: the automatic
  correction halves the daily error (42 % raw, 32 % manual shading, 16 to 17 %
  with the automatic correction).

## 1.3.1

- Automatic correction learned and applied by UTC hour (sun position) instead
  of the local clock hour. Shading follows the sun: with local hours, a
  daylight saving time change inside the 14-day window mixed two sun positions
  and blurred the morning and late-afternoon factors. The sensor still shows
  the factors with today's local hours. Factors saved by 1.3.0 are recomputed
  at startup.

## 1.3.0

- Automatic correction (option): learns a factor for each hour of the day from
  the actual production of the last 14 days (recorder statistics) against the
  archived forecasts (Open-Meteo Previous Runs API), recomputed every night.
  Starts from the manual shading factors, no change before 3 days of data,
  then weight = days / (days + 7); median of the daily ratios; outage days and
  low-sun hours skipped; factors between 0.3 and 1.2; saved across restarts.
- New diagnostic sensor "Correction factor" with the details per hour.
- The integration now depends on the recorder.

## 1.2.0

- New action `arome_solar_forecast.find_best_window`: best run of consecutive
  quarter hours for an appliance of a given duration, today or tomorrow, with
  optional appliance power cap, base load and time bounds. Returns start, end,
  usable and total solar energy.

## 1.1.0

- New Home Assistant integration, installable with HACS (`custom_components/arome_solar_forecast`):
  - setup and options in the user interface (up to three roof planes,
    efficiencies, temperature coefficient, hourly shading, weather model,
    update interval), English and French;
  - Energy dashboard solar forecast;
  - 13 sensors: energy today / tomorrow / remaining / current hour / next
    hour, power now / in 30 minutes / in 1 hour, peak power and time today
    and tomorrow, last update;
  - Solcast-format `detailedForecast` attributes, kept out of the recorder;
  - `arome_solar_forecast.get_forecast` action;
  - keeps the last forecast for up to 6 hours when Open-Meteo fails;
  - diagnostics with redacted location.
- Tests of the integration's forecast model, translations and manifest;
  hassfest and HACS validation in CI.

## 1.0.0

First public release.

- Home Assistant package: AROME forecast from Open-Meteo every 15 minutes,
  quarter-hour PV curve, energy today / tomorrow / remaining, power now and
  in one hour, peak power and time.
- PV model with cell temperature, efficiencies, inverter clipping, several
  roof planes and hourly shading factors.
- Solcast-compatible `detailedForecast` attribute (half hours, kW).
- Optional daily forecast check against actual production, for AROME and
  other forecasts.
- Generator (`tools/generate_package.py`), English and French entity names.
- Benchmark tool (`tools/benchmark.py`) on archived forecasts of several
  weather models, with results from a real installation (`docs/benchmark.md`).
- Scenario tests of the templates and repository privacy check in CI.

## Reference package

`packages/arome_solar_forecast.yaml` is generated with:

```bash
python tools/generate_package.py --latitude 45.76 --longitude 4.84 \
    --array kwp=3.0,tilt=30,azimuth=180 --inverter-kw 2.5 \
    --output packages/arome_solar_forecast.yaml
```

`examples/east_west_fr_with_check.yaml` is generated with:

```bash
python tools/generate_package.py --latitude 45.76 --longitude 4.84 \
    --array kwp=2.0,tilt=20,azimuth=90 --array kwp=2.0,tilt=20,azimuth=270 \
    --inverter-kw 3.0 --lang fr --shading 8=0.7,17=0.5 \
    --actual-entity sensor.solar_energy_today \
    --compare Solcast=sensor.solcast_pv_forecast_forecast_today \
    --output examples/east_west_fr_with_check.yaml
```
