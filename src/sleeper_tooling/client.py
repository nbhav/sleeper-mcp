from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import httpx

from sleeper_tooling.db import ApiResponseCache

Json = dict[str, Any] | list[Any]
TrendType = Literal["add", "drop"]
SeasonType = Literal["regular", "post", "pre", "off"]


class SleeperApiError(RuntimeError):
    """Raised when Sleeper returns an unsuccessful response."""


class SleeperClient:
    """Small synchronous client for Sleeper's documented and stats endpoints."""

    app_base_url = "https://api.sleeper.app/v1"
    data_base_url = "https://api.sleeper.com"

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
        cache: ApiResponseCache | None = None,
        refresh_cache: bool = False,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 sleeper-tooling/0.1 "
                    "(https://github.com/openai/codex)"
                ),
            },
        )
        self._cache = cache
        self._refresh_cache = refresh_cache

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
        if self._cache:
            self._cache.close()

    def __enter__(self) -> "SleeperClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_user(self, username_or_id: str) -> dict[str, Any]:
        return self._get_app(f"/user/{username_or_id}")

    def get_user_leagues(
        self,
        user_id: str,
        season: int | str,
        *,
        sport: str = "nfl",
    ) -> list[dict[str, Any]]:
        return self._get_app(f"/user/{user_id}/leagues/{sport}/{season}")

    def get_user_drafts(
        self,
        user_id: str,
        season: int | str,
        *,
        sport: str = "nfl",
    ) -> list[dict[str, Any]]:
        return self._get_app(f"/user/{user_id}/drafts/{sport}/{season}")

    def get_league(self, league_id: str) -> dict[str, Any]:
        return self._get_app(f"/league/{league_id}")

    def get_league_users(self, league_id: str) -> list[dict[str, Any]]:
        return self._get_app(f"/league/{league_id}/users")

    def get_rosters(self, league_id: str) -> list[dict[str, Any]]:
        return self._get_app(f"/league/{league_id}/rosters")

    def get_matchups(self, league_id: str, week: int) -> list[dict[str, Any]]:
        return self._get_app(f"/league/{league_id}/matchups/{week}")

    def get_transactions(self, league_id: str, week: int) -> list[dict[str, Any]]:
        return self._get_app(f"/league/{league_id}/transactions/{week}")

    def get_traded_picks(self, league_id: str) -> list[dict[str, Any]]:
        return self._get_app(f"/league/{league_id}/traded_picks")

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        return self._get_app(f"/draft/{draft_id}")

    def get_draft_picks(self, draft_id: str) -> list[dict[str, Any]]:
        return self._get_app(f"/draft/{draft_id}/picks")

    def get_draft_traded_picks(self, draft_id: str) -> list[dict[str, Any]]:
        return self._get_app(f"/draft/{draft_id}/traded_picks")

    def get_nfl_state(self) -> dict[str, Any]:
        return self._get_app("/state/nfl")

    def get_players(
        self,
        *,
        sport: str = "nfl",
        position: str | None = None,
        active: bool | None = None,
    ) -> dict[str, dict[str, Any]]:
        params: dict[str, str] = {}
        if position:
            params["position"] = position.upper()
        if active is True:
            params["active"] = "true"
        return self._get_app(f"/players/{sport}", params=params)

    def get_trending_players(
        self,
        trend_type: TrendType,
        *,
        sport: str = "nfl",
        lookback_hours: int = 24,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        return self._get_app(
            f"/players/{sport}/trending/{trend_type}",
            params={"lookback_hours": str(lookback_hours), "limit": str(limit)},
        )

    def get_stats(
        self,
        season: int | str,
        *,
        week: int | None = None,
        sport: str = "nfl",
        season_type: SeasonType = "regular",
        position: str | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._get_data(
            self._weekly_data_path("stats", sport, season, week),
            params=self._data_params(season_type, position, order_by),
        )

    def get_projections(
        self,
        season: int | str,
        *,
        week: int | None = None,
        sport: str = "nfl",
        season_type: SeasonType = "regular",
        position: str | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._get_data(
            self._weekly_data_path("projections", sport, season, week),
            params=self._data_params(season_type, position, order_by),
        )

    def _weekly_data_path(
        self,
        kind: Literal["stats", "projections"],
        sport: str,
        season: int | str,
        week: int | None,
    ) -> str:
        path = f"/{kind}/{sport}/{season}"
        if week is not None:
            path = f"{path}/{week}"
        return path

    def _data_params(
        self,
        season_type: SeasonType,
        position: str | None,
        order_by: str | None,
    ) -> dict[str, str | list[str]]:
        params: dict[str, str | list[str]] = {"season_type": season_type}
        if position:
            params["position[]"] = [position.upper()]
        if order_by:
            params["order_by"] = order_by
        return params

    def _get_app(self, path: str, *, params: dict[str, Any] | None = None) -> Json:
        return self._get(f"{self.app_base_url}{path}", params=params)

    def _get_data(self, path: str, *, params: dict[str, Any] | None = None) -> Json:
        return self._get(f"{self.data_base_url}{path}", params=params)

    def _get(self, url: str, *, params: dict[str, Any] | None = None) -> Json:
        request = self._client.build_request("GET", url, params=params)
        cache_key = str(request.url)
        if self._cache and not self._refresh_cache:
            cached_response = self._cache.get(cache_key)
            if cached_response is not None:
                return cached_response

        response = self._client.send(request)
        if response.status_code >= 400:
            raise SleeperApiError(
                f"Sleeper API request failed: {response.status_code} {response.url}"
            )
        payload = response.json()
        if self._cache:
            self._cache.set(
                cache_key,
                url=str(response.url),
                response=payload,
                ttl_seconds=cache_ttl_for_url(str(response.url)),
            )
        return payload


def cache_ttl_for_url(url: str) -> int:
    if "/state/nfl" in url:
        return 300
    if "/trending/" in url:
        return 300
    if "/players/nfl" in url:
        return 21600
    if "/stats/" in url or "/projections/" in url:
        return 900
    if "/matchups/" in url or "/transactions/" in url:
        return 900
    return 3600


def load_or_fetch_players(
    client: SleeperClient,
    *,
    cache_path: Path | None,
    position: str | None = None,
    active: bool | None = None,
) -> dict[str, dict[str, Any]]:
    """Load players from cache when present, otherwise fetch and optionally cache."""
    if cache_path and cache_path.exists():
        import json

        return json.loads(cache_path.read_text())

    players = client.get_players(position=position, active=active)
    if cache_path:
        import json

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(players, indent=2, sort_keys=True))
    return players
