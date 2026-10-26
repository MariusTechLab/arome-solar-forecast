# Changelog

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
