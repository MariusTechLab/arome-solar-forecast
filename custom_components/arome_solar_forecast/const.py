"""Constants for the AROME Solar Forecast integration."""

from typing import Final

DOMAIN: Final = "arome_solar_forecast"
VERSION: Final = "1.1.0"
API_URL: Final = "https://api.open-meteo.com/v1/forecast"
ATTRIBUTION: Final = "Weather data by Open-Meteo.com (CC BY 4.0), model by Météo-France"

CONF_MODEL: Final = "model"
CONF_KWP: Final = "kwp"
CONF_TILT: Final = "tilt"
CONF_AZIMUTH: Final = "azimuth"
CONF_KWP_2: Final = "kwp_2"
CONF_TILT_2: Final = "tilt_2"
CONF_AZIMUTH_2: Final = "azimuth_2"
CONF_KWP_3: Final = "kwp_3"
CONF_TILT_3: Final = "tilt_3"
CONF_AZIMUTH_3: Final = "azimuth_3"
CONF_INVERTER_KW: Final = "inverter_kw"
CONF_SYSTEM_EFFICIENCY: Final = "system_efficiency"
CONF_INVERTER_EFFICIENCY: Final = "inverter_efficiency"
CONF_TEMP_COEFFICIENT: Final = "temp_coefficient"
CONF_CELL_HEATING: Final = "cell_heating"
CONF_SHADING: Final = "shading"
CONF_SCAN_MINUTES: Final = "scan_minutes"

ARRAY_KEYS: Final = (
    (CONF_KWP, CONF_TILT, CONF_AZIMUTH),
    (CONF_KWP_2, CONF_TILT_2, CONF_AZIMUTH_2),
    (CONF_KWP_3, CONF_TILT_3, CONF_AZIMUTH_3),
)

DEFAULT_MODEL: Final = "meteofrance_arome_france"
DEFAULT_TILT: Final = 30.0
DEFAULT_AZIMUTH: Final = 180.0
DEFAULT_SYSTEM_EFFICIENCY: Final = 0.98
DEFAULT_INVERTER_EFFICIENCY: Final = 0.96
DEFAULT_TEMP_COEFFICIENT: Final = -0.004
DEFAULT_CELL_HEATING: Final = 0.03
DEFAULT_SCAN_MINUTES: Final = 15

# Weather models offered in the user interface, with their forecast horizon.
MODELS: Final = {
    "meteofrance_arome_france": 2,
    "meteofrance_seamless": 4,
    "best_match": 7,
    "icon_d2": 2,
    "icon_eu": 4,
    "knmi_harmonie_arome_europe": 2,
    "ecmwf_ifs": 7,
}

# Approximate extent of the AROME France domain.
AROME_LATITUDE: Final = (37.5, 55.4)
AROME_LONGITUDE: Final = (-12.0, 16.0)

# Keep using the last successful forecast for this long when the API fails.
STALE_AFTER_HOURS: Final = 6
