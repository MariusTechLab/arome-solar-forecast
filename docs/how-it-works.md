# How it works

## Data flow

```
Open-Meteo API (AROME, every 15 min)          one REST call per roof plane
   └─ sensor.pv_forecast_arome_raw_data       time, irradiance on the panel plane, air temperature
        └─ sensor.pv_forecast_arome_curve     192 quarter hours of AC power (kW), attribute detailedForecast15
             ├─ ..._today / ..._tomorrow      kWh + half-hour detailedForecast (Solcast format)
             ├─ ..._remaining_today           kWh from now to midnight
             ├─ ..._power_now / ..._in_1_hour W (long-term statistics)
             └─ ..._peak_today                W + peak_time
```

With the YAML package, everything runs inside Home Assistant: one `rest`
sensor and `template` sensors, no custom component, no cloud account, no API
key. The integration (`custom_components/arome_solar_forecast`) implements the
same model in Python (`forecast.py`) and adds the Energy dashboard forecast.

## Weather input

- **Model**: `meteofrance_arome_france` (Météo-France AROME, 0.025°, about
  2.5 km), refreshed hourly by Météo-France, served by
  [Open-Meteo](https://open-meteo.com/en/docs/meteofrance-api).
- **Variables**: `global_tilted_irradiance` (GTI, W/m² on the plane of the
  panels, computed by Open-Meteo from direct and diffuse radiation) and
  `temperature_2m`, both at 15-minute resolution (`minutely_15`).
- **Timing**: radiation values are the mean of the *preceding* 15 minutes. The
  package shifts them so that `period_start` is the start of the quarter hour,
  in local time.
- **Horizon**: AROME runs about 42 hours ahead, enough for today and
  tomorrow. For a longer horizon use `--model meteofrance_seamless` (AROME,
  then ARPEGE) with `--forecast-days 3` or more.
- **Coverage**: France and neighbouring areas (roughly 37.5–55.4° N,
  12° W–16° E). Elsewhere, use `--model best_match` or a regional model, and
  check the choice with the benchmark tool.

## PV model

For each quarter hour and each roof plane:

```
cell temperature = air temperature + cell_heating × G
DC power         = kWp × G / 1000 × (1 + temp_coefficient × (cell temperature − 25))
                   × system_efficiency × shading[local hour]
AC power         = min(inverter AC limit, Σ DC power × inverter_efficiency)
```

| Parameter | Default | Meaning |
|---|---|---|
| `kwp` | — | peak power of the modules of that plane (DC) |
| `tilt` | 30 | tilt from horizontal, degrees |
| `azimuth` | 180 | compass direction the panels face (see below) |
| `--inverter-kw` | none | AC limit of the inverter(s): clipping |
| `--system-efficiency` | 0.98 | soiling, wiring, mismatch, degradation |
| `--inverter-efficiency` | 0.96 | DC to AC conversion |
| `--temp-coefficient` | −0.004 /°C | power loss per degree above 25 °C (datasheet "Pmax") |
| `--cell-heating` | 0.03 °C per W/m² | 0.025 open rack, 0.03 roof mounted, 0.035 integrated |
| `--shading` | 1 for every hour | per local hour factor, e.g. `16=0.5,17=0.4` |

Shading is applied before clipping. The factors are static: measure them with
the benchmark tool or from a few clear days (actual ÷ forecast per hour), and
redo it twice a year, since the sun path changes with the seasons.

## Azimuth conventions

The same roof is written three different ways depending on the tool. Getting
it wrong mirrors the forecast curve around solar noon.

| Panels facing | Compass (this package) | Open-Meteo | Solcast |
|---|---|---|---|
| North | 0 | 180 | 0 |
| East | 90 | −90 | −90 |
| South-east | 135 | −45 | −135 |
| South | 180 | 0 | 180 (or −180) |
| South-south-west | 195 | 15 | 165 |
| West | 270 | 90 | 90 |

The generator takes a compass value and converts it. Solcast counts
counter-clockwise from north: a roof facing 165° on a compass (towards the
south-east) is **−165** in Solcast, not 165.

A phone compass laid flat against a wall reads the direction *along* the wall;
the panels face 90° from it. Enable "true north" in the compass settings.

## Update cadence and load

- One HTTP request every 15 minutes per roof plane, about 5 kB each: 96
  requests a day, far below Open-Meteo's free limit (10,000 per day,
  non-commercial use).
- Template rendering: the curve (192 points) is recomputed when the data
  change; the other sensors read the curve. The power and remaining sensors use
  `now()` and are refreshed every minute by Home Assistant.

## Entity ids

Entity ids are derived from the names. With the default English names:

| Entity | Unit | Notes |
|---|---|---|
| `sensor.pv_forecast_arome_today` | kWh | attribute `detailedForecast` (30 min, kW) |
| `sensor.pv_forecast_arome_tomorrow` | kWh | attribute `detailedForecast` |
| `sensor.pv_forecast_arome_remaining_today` | kWh | |
| `sensor.pv_forecast_arome_power_now` | W | `state_class: measurement` |
| `sensor.pv_forecast_arome_power_in_1_hour` | W | `state_class: measurement` |
| `sensor.pv_forecast_arome_peak_today` | W | attribute `peak_time` |
| `sensor.pv_forecast_arome_curve` | points | attribute `detailedForecast15` (15 min, kW) |
| `sensor.pv_forecast_arome_raw_data` | points | API response |

With `--lang fr`: `sensor.prevision_pv_arome_aujourd_hui`, `..._demain`,
`..._restante_aujourd_hui`, `..._puissance_maintenant`,
`..._puissance_dans_1_heure`, `..._pic_aujourd_hui`, `..._courbe`.

The `detailedForecast` attribute uses the same keys as the Solcast
integration (`period_start`, `pv_estimate` in kW, half hours), so templates
written for Solcast can switch source by changing the entity id. AROME is
deterministic: there are no `pv_estimate10` / `pv_estimate90` percentiles.

## Forecast check (optional)

With `--actual-entity` (and optionally `--compare LABEL=sensor.x`), the
package also records, every day:

| Time | Entities | Content |
|---|---|---|
| 00:05 | `..._check_previous_day_<source>` | the forecast for today, as known before midnight |
| 06:30 | `..._check_morning_<source>` | the forecast for today at decision time |
| 23:55 | `..._check_actual_production_today` | the actual energy of the day |
| 23:55 | `..._check_error_<horizon>_<source>` | (forecast − actual) / actual in %, attribute `error_kwh` |

All have `state_class: measurement`, so long-term statistics keep them
beyond the recorder retention.

## Limitations

- No shading model: use the static hourly factors, or post-process.
- No snow cover, no soiling dynamics, no inverter derating with heat.
- The YAML package cannot feed the Energy dashboard "forecast" line, which
  only accepts integrations: use the integration for that.
- The REST call fails if Open-Meteo is unreachable: the sensors become
  unavailable until the next successful call (automations should have a
  fallback, as for any cloud forecast).
