"""Minimal emulation of the Home Assistant template functions used by the package.

Only what the generated templates need: states(), state_attr(), now(),
as_datetime(), timedelta, the float/as_local filters and the search/list tests.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment
from jinja2.sandbox import ImmutableSandboxedEnvironment

_SENTINEL = object()


class FakeHass:
    """Holds entity states and attributes, and a frozen clock."""

    def __init__(self, now: datetime, tz: str = "Europe/Paris") -> None:
        self.tz = ZoneInfo(tz)
        self.now = now.astimezone(self.tz)
        self.entities: dict[str, tuple[str, dict[str, Any]]] = {}

    def set(self, entity_id: str, state: Any, attributes: dict[str, Any] | None = None) -> None:
        self.entities[entity_id] = (str(state), attributes or {})

    def env(self) -> Environment:
        env = ImmutableSandboxedEnvironment()

        def states(entity_id: str) -> str:
            return self.entities.get(entity_id, ("unknown", {}))[0]

        def state_attr(entity_id: str, name: str) -> Any:
            return self.entities.get(entity_id, ("unknown", {}))[1].get(name)

        def as_datetime(value: Any) -> datetime | None:
            if isinstance(value, datetime):
                return value
            try:
                return datetime.fromisoformat(str(value))
            except ValueError:
                return None

        def as_local(value: datetime) -> datetime:
            return value.astimezone(self.tz)

        def to_float(value: Any, default: Any = _SENTINEL) -> Any:
            try:
                return float(value)
            except (TypeError, ValueError):
                if default is _SENTINEL:
                    raise
                return default

        def today_at(value: str = "00:00") -> datetime:
            parts = [int(p) for p in value.split(":")] + [0, 0]
            return self.now.replace(hour=parts[0], minute=parts[1], second=parts[2], microsecond=0)

        env.globals.update(
            states=states,
            state_attr=state_attr,
            today_at=today_at,
            now=lambda: self.now,
            as_datetime=as_datetime,
            timedelta=timedelta,
            none=None,
        )
        env.filters.update(float=to_float, as_local=as_local, as_datetime=as_datetime)
        env.tests.update(
            search=lambda value, pattern: bool(re.search(pattern, str(value))),
            list=lambda value: isinstance(value, list),
        )
        return env

    def render(self, template: str) -> Any:
        """Render like Home Assistant: the output is parsed back to a native type."""
        text = self.env().from_string(template).render().strip()
        return _native(text)


def _native(text: str) -> Any:
    import ast

    if text in ("None", ""):
        return None
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return text
