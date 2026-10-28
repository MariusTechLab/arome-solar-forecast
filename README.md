# AROME solar forecast for Home Assistant

[![Validate](https://github.com/MariusTechLab/arome-solar-forecast/actions/workflows/validate.yml/badge.svg)](https://github.com/MariusTechLab/arome-solar-forecast/actions/workflows/validate.yml)
[![HACS](https://img.shields.io/badge/HACS-custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

*[Version française](README.fr.md)*

A free photovoltaic production forecast for Home Assistant, built on
**Météo-France's AROME model** (2.5 km grid) through the
[Open-Meteo](https://open-meteo.com/) API. No account, no API key, no daily
call quota, an update every 15 minutes. Two ways to use it:

- the **integration**, installed with HACS and set up in the user interface,
  which also feeds the **Energy dashboard** forecast;
- a plain **YAML package**, for those who prefer no custom component.

On a real installation in the south of France, AROME's forecast **issued the
day before** missed the daily production by **17 %** on average over 17
autumn days, against **21 %** for the Solcast morning forecast and 20–31 %
for other weather models ([benchmark](docs/benchmark.md)). The repository
includes the tool to run the same test on your own data.

## Features

- **Quarter-hour forecast** for today and tomorrow, in kW, from irradiance on
  the plane of your panels and air temperature.
- **Physical PV model**: cell temperature, temperature coefficient, system and
  inverter efficiency, **inverter clipping**, **several roof planes**
  (east/west...), optional **hourly shading factors**.
- **Ready-to-use sensors**: energy today, tomorrow, remaining today, current
  and next hour; power now, in 30 minutes and in one hour; peak power and peak
  time today and tomorrow.
- **Energy dashboard forecast** (integration): the expected production is
  drawn next to the actual one.
- **Solcast-compatible attribute**: `detailedForecast` with `period_start` and
  `pv_estimate` (kW, half hours) on the today and tomorrow sensors, so
  templates written for Solcast work by changing the entity id. The
  `arome_solar_forecast.get_forecast` action returns the same list.
- **Robust**: the integration keeps the last forecast for up to 6 hours if
  Open-Meteo is unreachable, and keeps the large forecast attributes out of the
  history database.
- **Built-in forecast check** (package): records the previous-day and
  morning forecasts, the actual production and the daily error, for AROME and
  for any other forecast you want to compare (Solcast, Forecast.Solar...).
- **Benchmark tool**: ranks 8+ weather models against your own production
  history, using archived forecasts.
- **English and French** user interface and entity names.
- **Tested**: the integration's model and every package template are checked
  against an independent implementation of the formula, in CI; hassfest and
  HACS validation on every commit.

## Requirements

- A location covered by AROME: France and neighbouring areas, roughly
  37.5–55.4° N and 12° W–16° E. Elsewhere, choose another weather model
  (Open-Meteo best match, ICON-D2, ICON-EU, Harmonie, ECMWF); check with the
  benchmark which one fits your site.
- Integration: Home Assistant 2025.2 or newer and HACS.
- Package: Home Assistant 2024.10 or newer, and Python 3.10+ on any computer
  to run the generator (optional: the package can also be edited by hand).

## Installation with HACS (recommended)

In one click, from a browser where your Home Assistant is open:

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=MariusTechLab&repository=arome-solar-forecast&category=integration)

Then, after downloading and restarting:

[![Open your Home Assistant instance and start setting up AROME Solar Forecast.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=arome_solar_forecast)

Or by hand:

1. In HACS, open the menu (⋮) → **Custom repositories**, add
   `https://github.com/MariusTechLab/arome-solar-forecast` with the type
   **Integration**.
2. Search for **AROME Solar Forecast**, download it and restart Home
   Assistant.
3. **Settings → Devices & services → Add integration → AROME Solar
   Forecast.** Enter the peak power, tilt and azimuth of your panels (compass:
   180 = south) and the inverter limit. The location defaults to your home.
4. Optional, in the integration's **Configure** menu: second and third roof
   planes, efficiencies, temperature coefficient, hourly shading factors,
   weather model, update interval.
5. Energy dashboard: **Settings → Dashboards → Energy → Solar panels →
   your production sensor → Solar production forecast → AROME Solar
   Forecast.**

Several installations (houses, roofs) can be added as separate entries.

## Installation as a YAML package

### 1. Generate the package

```bash
python tools/generate_package.py \
    --latitude 45.76 --longitude 4.84 \
    --array kwp=3.0,tilt=30,azimuth=180 \
    --inverter-kw 2.5 \
    --output arome_solar_forecast.yaml
```

- `azimuth` is the compass direction the panels face: 180 = south,
  90 = east, 270 = west. See the [conventions table](docs/how-it-works.md#azimuth-conventions):
  Open-Meteo and Solcast use other conventions.
- Repeat `--array` for each roof plane, for example
  `--array kwp=2,tilt=20,azimuth=90 --array kwp=2,tilt=20,azimuth=270`.
- `--lang fr` for French entity names. All options:
  `python tools/generate_package.py --help` and the
  [configuration reference](#configuration-reference).

Two decimals of latitude and longitude (about 1 km) are enough: the AROME grid
is 0.025° (about 2.5 km).

### 2. Add it to Home Assistant

1. Copy the file into your configuration folder, for example
   `config/packages/arome_solar_forecast.yaml`.
2. Enable packages in `configuration.yaml` if not done yet:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

3. Check the configuration (**Developer tools → YAML → Check configuration**)
   and **restart** Home Assistant. A restart is required the first time,
   because it loads the `rest` integration.
4. Optional: exclude the two bulky technical sensors from the history database
   ([examples/recorder.yaml](examples/recorder.yaml)).

The sensors are available within a minute of the restart.

### Without Python

Copy [packages/arome_solar_forecast.yaml](packages/arome_solar_forecast.yaml)
and edit:

- in the `resource:` URL: `latitude`, `longitude`, `tilt` and `azimuth`
  (**Open-Meteo convention**: 0 = south, −90 = east, 90 = west);
- at the top of the `detailedForecast15` template: `kwp` in `arrays`,
  `ac_limit` (kW, or `none`), the efficiencies and the 24 `shading` factors
  (index = local hour).

## Sensors

### Integration

Entity ids follow the entry name and the language of Home Assistant; with the
default name and English: `sensor.arome_solar_forecast_energy_today`...

| Sensor | Unit | Description |
|---|---|---|
| Energy today / tomorrow | kWh | attributes `detailedForecast` (30 min), `detailedForecast15` (15 min), `complete` |
| Energy remaining today | kWh | from now to midnight |
| Energy current hour / next hour | kWh | |
| Power now / in 30 minutes / in 1 hour | W | long-term statistics |
| Peak power today / tomorrow | W | |
| Peak time today / tomorrow | timestamp | |
| Last update | timestamp | diagnostic; attribute `stale` when the last call failed |

Action `arome_solar_forecast.get_forecast` (`days`, `resolution`: `30min` or
`15min`) returns `{model, updated, forecast: [{period_start, pv_estimate}]}`.

### Automatic correction

In **Configure**, tick **Automatic correction** and pick your PV production
sensor (energy in kWh, total or daily, or power in W or kW, with long-term
statistics). Every night at 03:17, and at startup, the integration compares
the archived AROME forecasts of the last 14 days with your actual production,
hour by hour, and learns a correction factor for each time slot of the day: 30 minutes by
default, or 15 minutes or 1 hour (option **Correction resolution**; below one
hour it uses the 5-minute statistics, kept 10 days by default by the recorder). It
captures shading (trees, buildings), soiling or a capacity lower than
declared, as Solcast's automated dampening does, without using any API quota.

It is careful while data is scarce:

- the starting point is your manual shading factors (1 by default);
- no change for an hour until 3 days of data exist for it;
- then a gradual move towards the measured value: weight = days / (days + 7),
  about half way after 7 days and two thirds after 14;
- the median of the daily ratios is used, so an inverter outage or one
  unexpectedly sunny day does not pull the factor;
- days producing almost nothing while sun was forecast are treated as outages
  and skipped; hours forecast below 5 % of the peak power are ignored;
- factors stay between 0.3 and 1.2;
- hours are counted in solar time (UTC), so the factors stay right across
  daylight saving time changes; the sensor shows them with local hours.

The diagnostic sensor **Correction factor** shows the mean factor; its
attributes give the factor, number of days, median ratio and weight for each
hour. The factors are saved and survive a restart.

### Best time to run an appliance

Action `arome_solar_forecast.find_best_window` finds the run of consecutive
quarter hours with the most usable solar energy, today (from now on) or
tomorrow:

```yaml
action: arome_solar_forecast.find_best_window
data:
  duration: 75          # minutes, rounded up to a quarter hour
  day: tomorrow         # today or tomorrow
  power_kw: 2.0         # optional: solar above the appliance power is not counted
  base_load_kw: 0.35    # optional: house consumption subtracted first
  after: "10:00:00"     # optional bounds
  before: "17:00:00"
response_variable: window
```

It returns `found`, `start`, `end`, `duration_minutes`, `usable_energy_kwh`
and `solar_energy_kwh`. The earliest window wins a tie. Example automation:
plan the dishwasher in the evening for tomorrow's best window, then start it
with a time trigger on `window.start`.

### YAML package

| Entity | Unit | Description |
|---|---|---|
| `sensor.pv_forecast_arome_today` | kWh | forecast energy today; attribute `detailedForecast` |
| `sensor.pv_forecast_arome_tomorrow` | kWh | forecast energy tomorrow; attribute `detailedForecast` |
| `sensor.pv_forecast_arome_remaining_today` | kWh | from now to midnight |
| `sensor.pv_forecast_arome_power_now` | W | forecast power for the current quarter hour |
| `sensor.pv_forecast_arome_power_in_1_hour` | W | forecast power one hour from now |
| `sensor.pv_forecast_arome_peak_today` | W | highest quarter hour; attribute `peak_time` |
| `sensor.pv_forecast_arome_curve` | - | attribute `detailedForecast15`: 192 quarter hours |
| `sensor.pv_forecast_arome_raw_data` | - | Open-Meteo response (technical) |

Details: [how it works](docs/how-it-works.md).

## Using the forecast

Energy expected for the rest of the day:

```yaml
condition: numeric_state
entity_id: sensor.pv_forecast_arome_remaining_today
above: 3
```

Forecast power of each half hour, like Solcast:

```jinja
{% for x in state_attr('sensor.pv_forecast_arome_today', 'detailedForecast') %}
  {{ x.period_start }}: {{ x.pv_estimate }} kW
{% endfor %}
```

Ready-made examples:

- [examples/best_window.yaml](examples/best_window.yaml): best start time
  today for an appliance of a given power and duration;
- [examples/dashboard.yaml](examples/dashboard.yaml): entities card, forecast
  vs actual graph, daily errors, and an ApexCharts curve;
- [examples/east_west_fr_with_check.yaml](examples/east_west_fr_with_check.yaml):
  a generated package with two roof planes, French names, shading factors and
  a daily comparison with Solcast.

A forecast is never perfect. In automations, keep a fallback for when the
sensors are unavailable, and prefer real-time measurements (grid export) for
short-term decisions.

## Comparing forecasts on your installation

### Live, from now on

Add the forecast check when generating the package:

```bash
python tools/generate_package.py ... \
    --actual-entity sensor.solar_energy_today \
    --compare Solcast=sensor.solcast_pv_forecast_forecast_today
```

`--actual-entity` is a sensor of the day's actual PV energy (kWh, reset at
midnight; a `utility_meter` with a daily cycle on your production counter
works). Every day the package records the forecasts at 00:05 and 06:30, then
the actual production and the error of each source at 23:55.

### On past data

```bash
python tools/benchmark.py --csv history.csv --entity sensor.pv_power \
    --latitude 45.76 --longitude 4.84 --array kwp=3,tilt=30,azimuth=180
```

The CSV is the **Download data** export of the Home Assistant History page for
your PV power sensor. See the [benchmark guide](docs/benchmark.md).

## Configuration reference

| Option | Default | Description |
|---|---|---|
| `--latitude`, `--longitude` | required | location |
| `--array kwp=,tilt=,azimuth=` | `kwp=3,tilt=30,azimuth=180` | one per roof plane; compass azimuth |
| `--inverter-kw` | none | inverter AC limit in kW (clipping) |
| `--system-efficiency` | 0.98 | soiling, wiring, mismatch |
| `--inverter-efficiency` | 0.96 | DC to AC |
| `--temp-coefficient` | −0.004 | power change per °C above 25 °C |
| `--cell-heating` | 0.03 | °C of cell heating per W/m² |
| `--shading` | none | factors per local hour, e.g. `8=0.7,16=0.5,17=0.4` |
| `--model` | `meteofrance_arome_france` | any Open-Meteo model |
| `--forecast-days` | 2 | 2 for AROME; up to 7 with `meteofrance_seamless` |
| `--scan-interval` | 900 | seconds between API calls (minimum 600) |
| `--lang` | `en` | `en` or `fr` entity names |
| `--title` | per language | name prefix of the entities |
| `--actual-entity` | none | enables the daily forecast check |
| `--compare LABEL=entity` | none | other forecasts to check (repeatable) |
| `--morning-time` | `06:30:00` | time of the morning snapshot |
| `--output` | stdout | file to write |

## FAQ

**The sensors stay unavailable.** Check that Home Assistant was restarted
(not only reloaded) after adding the first `rest` sensor, and look for
`rest` errors in the log. Open the `resource` URL in a browser: it must
return JSON with a `minutely_15` block.

**The forecast is constantly too high.** Compare kWp and inverter limit with
your installation, then run the benchmark: the fitted scale shows the
systematic gap. A gap concentrated in the morning or late afternoon is
shading: use `--shading`.

**The curve is shifted in time.** Most often the azimuth sign: see the
[conventions table](docs/how-it-works.md#azimuth-conventions).

**Can it feed the Energy dashboard forecast?** Yes, with the integration
(see the installation steps). The YAML package cannot: that line only accepts
integrations.

**Integration or package?** The integration for a setup in the user
interface, the Energy dashboard and resilience to API outages. The package for
an installation without custom component, or to adapt the templates.

**Can I use it outside France?** Yes, with another model (`--model
best_match`, `icon_d2`, `knmi_harmonie_arome_europe`...). Run the benchmark to
choose.

**Is there a P10/P90?** No: AROME is a deterministic model.

## Data sources and terms

- Weather data by [Open-Meteo.com](https://open-meteo.com/), licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The free API is
  for non-commercial use, up to 10,000 calls a day; this package makes 96 per
  roof plane.
- AROME and ARPEGE models by [Météo-France](https://meteofrance.com/), open
  data under the Etalab Open Licence 2.0.

This project is not affiliated with Open-Meteo, Météo-France or Solcast.

## Related project

[Solar surplus climate control](https://github.com/MariusTechLab/solar-surplus-climate-control):
a blueprint that runs a reversible air conditioner on PV surplus, with
optional pre-cooling based on a remaining-production forecast such as
`sensor.pv_forecast_arome_remaining_today`.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python tools/validate_repo.py
```

After changing the generator, regenerate the reference package with the
command in [CHANGELOG.md](CHANGELOG.md); a test checks that it is up to date.

## License

[MIT](LICENSE)
