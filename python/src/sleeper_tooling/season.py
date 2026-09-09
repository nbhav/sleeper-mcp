from __future__ import annotations

from datetime import date


def current_season_year() -> int:
    return date.today().year
