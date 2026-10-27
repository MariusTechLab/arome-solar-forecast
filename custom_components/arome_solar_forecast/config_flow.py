"""Config and options flows."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import (
    AROME_LATITUDE,
    AROME_LONGITUDE,
    CONF_AUTO_CORRECTION,
    CONF_AZIMUTH,
    CONF_AZIMUTH_2,
    CONF_AZIMUTH_3,
    CONF_CELL_HEATING,
    CONF_INVERTER_EFFICIENCY,
    CONF_INVERTER_KW,
    CONF_KWP,
    CONF_KWP_2,
    CONF_KWP_3,
    CONF_MODEL,
    CONF_PRODUCTION_ENTITY,
    CONF_SCAN_MINUTES,
    CONF_SHADING,
    CONF_SYSTEM_EFFICIENCY,
    CONF_TEMP_COEFFICIENT,
    CONF_TILT,
    CONF_TILT_2,
    CONF_TILT_3,
    DEFAULT_AZIMUTH,
    DEFAULT_CELL_HEATING,
    DEFAULT_INVERTER_EFFICIENCY,
    DEFAULT_MODEL,
    DEFAULT_SCAN_MINUTES,
    DEFAULT_SYSTEM_EFFICIENCY,
    DEFAULT_TEMP_COEFFICIENT,
    DEFAULT_TILT,
    DOMAIN,
    MODELS,
)
from .coordinator import fetch_forecast
from .forecast import PvArray, format_shading, has_irradiance, parse_shading


def _number(minimum: float, maximum: float, step: float | str, unit: str | None = None) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


KWP = _number(0, 1000, 0.01, "kWp")
TILT = _number(0, 90, 1, "°")
AZIMUTH = _number(0, 360, 1, "°")
MODEL = selector.SelectSelector(selector.SelectSelectorConfig(
    options=list(MODELS), translation_key="model", mode=selector.SelectSelectorMode.DROPDOWN))


def _system_schema(d: dict[str, Any], hass_lat: float, hass_lon: float, first_step: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if first_step:
        fields[vol.Required(CONF_NAME, default=d.get(CONF_NAME, "AROME Solar Forecast"))] = str
    fields.update({
        vol.Required(CONF_LATITUDE, default=d.get(CONF_LATITUDE, round(hass_lat, 4))): selector.NumberSelector(
            selector.NumberSelectorConfig(min=-90, max=90, step="any", mode=selector.NumberSelectorMode.BOX)),
        vol.Required(CONF_LONGITUDE, default=d.get(CONF_LONGITUDE, round(hass_lon, 4))): selector.NumberSelector(
            selector.NumberSelectorConfig(min=-180, max=180, step="any", mode=selector.NumberSelectorMode.BOX)),
        vol.Required(CONF_MODEL, default=d.get(CONF_MODEL, DEFAULT_MODEL)): MODEL,
        vol.Required(CONF_KWP, default=d.get(CONF_KWP, 3.0)): _number(0.01, 1000, 0.01, "kWp"),
        vol.Required(CONF_TILT, default=d.get(CONF_TILT, DEFAULT_TILT)): TILT,
        vol.Required(CONF_AZIMUTH, default=d.get(CONF_AZIMUTH, DEFAULT_AZIMUTH)): AZIMUTH,
        vol.Required(CONF_INVERTER_KW, default=d.get(CONF_INVERTER_KW, 0)): _number(0, 1000, 0.01, "kW"),
    })
    if first_step:
        return vol.Schema(fields)
    fields.update({
        vol.Required(CONF_KWP_2, default=d.get(CONF_KWP_2, 0)): KWP,
        vol.Required(CONF_TILT_2, default=d.get(CONF_TILT_2, DEFAULT_TILT)): TILT,
        vol.Required(CONF_AZIMUTH_2, default=d.get(CONF_AZIMUTH_2, DEFAULT_AZIMUTH)): AZIMUTH,
        vol.Required(CONF_KWP_3, default=d.get(CONF_KWP_3, 0)): KWP,
        vol.Required(CONF_TILT_3, default=d.get(CONF_TILT_3, DEFAULT_TILT)): TILT,
        vol.Required(CONF_AZIMUTH_3, default=d.get(CONF_AZIMUTH_3, DEFAULT_AZIMUTH)): AZIMUTH,
        vol.Required(CONF_SYSTEM_EFFICIENCY, default=d.get(CONF_SYSTEM_EFFICIENCY, DEFAULT_SYSTEM_EFFICIENCY)):
            _number(0.5, 1, 0.01),
        vol.Required(CONF_INVERTER_EFFICIENCY,
                     default=d.get(CONF_INVERTER_EFFICIENCY, DEFAULT_INVERTER_EFFICIENCY)): _number(0.5, 1, 0.01),
        vol.Required(CONF_TEMP_COEFFICIENT, default=d.get(CONF_TEMP_COEFFICIENT, DEFAULT_TEMP_COEFFICIENT)):
            _number(-0.01, 0, "any", "/°C"),
        vol.Required(CONF_CELL_HEATING, default=d.get(CONF_CELL_HEATING, DEFAULT_CELL_HEATING)):
            _number(0, 0.06, 0.001, "°C/(W/m²)"),
        vol.Optional(CONF_SHADING, description={"suggested_value": d.get(CONF_SHADING, "")}): str,
        vol.Required(CONF_AUTO_CORRECTION, default=d.get(CONF_AUTO_CORRECTION, False)): selector.BooleanSelector(),
        vol.Optional(CONF_PRODUCTION_ENTITY, description={"suggested_value": d.get(CONF_PRODUCTION_ENTITY)}):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class=["energy", "power"])),
        vol.Required(CONF_SCAN_MINUTES, default=d.get(CONF_SCAN_MINUTES, DEFAULT_SCAN_MINUTES)):
            _number(10, 60, 5, "min"),
    })
    return vol.Schema(fields)


async def _validate(flow: ConfigFlow | OptionsFlow, data: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Return (errors, description placeholders) after a test request."""
    errors: dict[str, str] = {}
    if CONF_SHADING in data:
        try:
            data[CONF_SHADING] = format_shading(parse_shading(data[CONF_SHADING]))
        except ValueError:
            errors[CONF_SHADING] = "invalid_shading"
            return errors, {}
    if data.get(CONF_AUTO_CORRECTION) and not data.get(CONF_PRODUCTION_ENTITY):
        errors[CONF_PRODUCTION_ENTITY] = "production_required"
        return errors, {}
    lat, lon, model = float(data[CONF_LATITUDE]), float(data[CONF_LONGITUDE]), data[CONF_MODEL]
    if model.startswith("meteofrance_arome") and not (
        AROME_LATITUDE[0] <= lat <= AROME_LATITUDE[1] and AROME_LONGITUDE[0] <= lon <= AROME_LONGITUDE[1]
    ):
        errors[CONF_MODEL] = "outside_arome"
        return errors, {}
    array = PvArray(float(data[CONF_KWP]), float(data[CONF_TILT]), float(data[CONF_AZIMUTH]))
    try:
        response = await fetch_forecast(async_get_clientsession(flow.hass), lat, lon, model, array)
    except (aiohttp.ClientError, TimeoutError, UpdateFailed):
        errors["base"] = "cannot_connect"
        return errors, {}
    if not has_irradiance(response):
        errors[CONF_MODEL] = "no_data"
    return errors, {}


class AromeSolarForecastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one PV system."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, _ = await _validate(self, user_input)
            if not errors:
                await self.async_set_unique_id(
                    f"{float(user_input[CONF_LATITUDE]):.3f}_{float(user_input[CONF_LONGITUDE]):.3f}_"
                    f"{float(user_input[CONF_KWP]):g}_{float(user_input[CONF_TILT]):g}_"
                    f"{float(user_input[CONF_AZIMUTH]):g}")
                self._abort_if_unique_id_configured()
                title = user_input.pop(CONF_NAME)
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=_system_schema(user_input or {}, self.hass.config.latitude, self.hass.config.longitude, True),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AromeSolarForecastOptionsFlow()


class AromeSolarForecastOptionsFlow(OptionsFlow):
    """Edit every parameter, add up to two more roof planes."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, _ = await _validate(self, user_input)
            if not errors:
                user_input.setdefault(CONF_SHADING, "")
                return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            data_schema=_system_schema(current, self.hass.config.latitude, self.hass.config.longitude, False),
            errors=errors,
        )
