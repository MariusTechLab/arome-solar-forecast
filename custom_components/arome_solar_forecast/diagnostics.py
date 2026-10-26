"""Diagnostics download."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant

from . import AromeConfigEntry

TO_REDACT = {CONF_LATITUDE, CONF_LONGITUDE}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: AromeConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    curve = data.curve if data else []
    return {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "model": coordinator.model,
        "system": {
            "arrays": [vars(a) for a in coordinator.system.arrays],
            "inverter_kw": coordinator.system.inverter_kw,
            "shading": list(coordinator.system.shading),
        },
        "last_update": data.fetched_at.isoformat() if data else None,
        "stale": data.stale if data else None,
        "slots": len(curve),
        "first_slot": curve[0].start.isoformat() if curve else None,
        "last_slot": curve[-1].start.isoformat() if curve else None,
        "max_kw": max((s.kw for s in curve), default=None),
    }
