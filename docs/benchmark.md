# Benchmark: which forecast follows the weather best?

Results from one real installation, and how to reproduce the test on yours
with `tools/benchmark.py`.

## Site and method

- **Installation**: 2.9 kWp residential system, 2.3 kW inverter, 18° tilt,
  facing south, in the south of France. Some shading early in the morning and
  late in the afternoon.
- **Actual production**: hourly statistics of the inverter gateway, from late
  August to late October 2026.
- **Forecasts**: archived runs of each weather model from the Open-Meteo
  [Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api), at
  the site's coordinates.
  - **day-1**: forecast issued about 24 hours before (what you know the
    evening before);
  - **day0**: latest run before each hour (closer to what an automation sees
    during the day).
- **Solcast**: the Solcast integration's "forecast today" read at 07:00 each
  morning, and its "power now" sensor (updated during the day).
- **Calibration**: one scale factor per source, fitted on 27 August to
  30 September. **Test**: 1 to 25 October, 17 complete days (days with
  gateway outages excluded). The scale factor removes a constant bias, so the
  ranking reflects how well each source follows the weather, not how well its
  capacity was declared.
- **Irradiance**: global horizontal irradiance with the fitted scale factor.
  On this site it did slightly better than the tilted irradiance computed for
  an assumed 30° south plane (AROME day-1: 17 % against 20 %), because the
  scale factor absorbs orientation and shading. The benchmark tool uses the
  tilted irradiance of the orientation you declare.
- **Metric**: mean absolute error of the daily energy, in % of the mean daily
  production (6.1 kWh), and mean absolute hourly error in kW.

## Results

Daily energy error, test period (lower is better):

| Source | Previous day (day-1) | Latest run (day0) |
|---|---|---|
| **Météo-France AROME** | **17 %** | **14 %** |
| GFS (NOAA) | 20 % | 21 % |
| UKMO 10 km (Met Office) | 20 % | 19 % |
| Solcast, 07:00 forecast | 21 % (same morning) | - |
| Solcast, updated during the day | - | 20 % |
| ICON-EU (DWD) | 22 % | 18 % |
| ECMWF IFS 0.25° | 24 % | 21 % |
| ECMWF IFS (HRES) | 23 % | 19 % |
| ICON-D2 (DWD, 2 km) | 24 % | 16 % |
| ARPEGE Europe (Météo-France) | 25 % | 17 % |
| Harmonie AROME (KNMI / DMI) | 26 % | 18–23 % |
| GEM (Canada) | 28 % | 20 % |
| Open-Meteo `best_match` | 24 % | 16 % |
| ECMWF AIFS (AI model) | 30 % | 27 % |
| CMA GRAPES (China) | 31 % | 38 % |

Hourly error: 0.17 to 0.21 kW for the best sources (AROME, Solcast, ICON-D2);
the difference between them is small at the hourly scale.

Day by day, AROME (previous day) was closer to the actual production than the
Solcast morning forecast on **11 of 17 days**. The largest gaps were on grey
days, where Solcast was too optimistic (e.g. 6.2 kWh forecast, 4.7 for AROME,
3.6 actual).

Averaging models did **not** help: AROME alone 17 %, AROME + GFS 17 %,
AROME + Solcast 18 %, AROME + ICON-EU, ECMWF or ARPEGE 19 %, mean of six
models 19 %.

## What we learned

1. **Use AROME in France.** It was the most accurate source, ahead of
   Solcast, even with a forecast issued the day before.
2. **The fine grid matters for clouds.** The global AI model (ECMWF AIFS,
   0.25°) was among the worst on this site: autumn low clouds and fog are
   local.
3. **A constant correction factor is not enough.** The ratio actual ÷
   Solcast forecast, by hour of the day:

   | Hour (UTC) | August | September | October |
   |---|---|---|---|
   | 06–07 | 0.75–0.85 | 0.61–0.79 | 0.40–0.47 |
   | 08–14 | 0.86–0.98 | 0.85–0.91 | 0.77–0.96 |
   | 15 | 0.77 | 0.65 | 0.50 |
   | 16–17 | 0.55–0.58 | 0.41 | 0.37 |

   Midday was within 5–15 %, but the late afternoon lost half its energy to
   shading that no forecast service knows about, and the loss grew as the sun
   got lower. Hourly factors (`--shading`) or Solcast's automated dampening fix
   this; a single factor (0.7 here) was too harsh at noon and too lenient in the
   evening.
4. **Check the azimuth convention.** The Solcast site had been declared at
   165, which Solcast reads as 15° west of south, for a roof measured at 165°
   on a compass (15° east of south). See
   [the conventions table](how-it-works.md#azimuth-conventions).
5. **Better timing did not pay in autumn.** Replaying a water heater (3 kW,
   45 min) over 16 days, starting at the best forecast window instead of
   "forecast peak − 15 min" made no measurable difference (between −1.2 and
   +0.2 kWh of solar energy over the period, depending on how the house load
   is counted), and a fixed 13:00 start did as well as both. A perfect
   schedule, knowing the actual production, would have gained about 3.5 kWh:
   with low, flat autumn production the limit is forecast uncertainty, not the
   scheduling rule.

## Automatic correction: walk-forward test

Same installation, 23 to 28 October 2026. For each day, the correction
factors are learned from the 14 previous days only, then applied to that day's
archived AROME forecast, and compared with the actual production (5-minute
statistics summed by quarter hour).

| Method | Error per quarter hour | Mean daily energy error |
|---|---|---|
| AROME raw | 0.063 kWh | 42.5 % |
| Manual hourly shading (2 factors) | 0.056 kWh | 32.2 % |
| Automatic correction, 1 hour | 0.053 kWh | 16.9 % |
| Automatic correction, 30 minutes | 0.053 kWh | 16.8 % |
| Automatic correction, 15 minutes | 0.053 kWh | 16.4 % |

The automatic correction halves the daily error. On this site, the slot
length barely matters; 30 minutes is the default as a compromise between
following a moving shadow and the amount of data per slot.

## Limits of this benchmark

One site, one season, 17 test days. The ranking can differ elsewhere, in
summer, or in mountains and coastal areas. Solcast's own tuning (automated
dampening, correct azimuth) was enabled only after the test. Run the tool on
your own data before choosing.

## Run it on your installation

1. In Home Assistant, open **History**, select your PV power sensor (W or
   kW), choose a period of at least two weeks (more is better) and use
   **Download data** to get a CSV file.
2. Run:

   ```bash
   python tools/benchmark.py --csv history.csv --entity sensor.pv_power \
       --latitude 45.76 --longitude 4.84 \
       --array kwp=3,tilt=30,azimuth=180 --inverter-kw 2.5
   ```

   Add `--reference Solcast=sensor.solcast_pv_forecast_power_now` if that
   sensor is in the same export, `--models` to choose the models, `--json` for
   machine-readable output. On Windows, install `tzdata` first
   (`pip install tzdata`).
3. Read the table: the **daily error** ranks the sources; the **scale** shows
   how far the physical model is from your real output (1 = unbiased); a large
   positive **raw bias** points to shading, soiling or a capacity lower than
   declared.

The Previous Runs API keeps an archive of past runs (see its documentation for
the available period and models). The recorder of
Home Assistant keeps 10 days of history by default; export regularly or
increase `purge_keep_days` to benchmark over longer periods.
