"""Solar forecast for the Energy dashboard."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .forecast import hourly_wh


async def async_get_solar_forecast(hass: HomeAssistant, config_entry_id: str) -> dict[str, Any] | None:
    """Return the forecast energy per hour (Wh) for the Energy dashboard."""
    entry = hass.config_entries.async_get_entry(config_entry_id)
    if entry is None or entry.domain != DOMAIN or not getattr(entry, "runtime_data", None):
        return None
    data = entry.runtime_data.data
    if not data:
        return None
    return {"wh_hours": {hour.isoformat(): wh for hour, wh in hourly_wh(data.curve).items()}}
