from __future__ import annotations

import httpx
import pytest

from sleeper_tooling.client import (
    SleeperApiError,
    SleeperClient,
    cache_ttl_for_url,
    load_or_fetch_players,
)
from sleeper_tooling.db import ApiResponseCache


def test_get_user_uses_documented_app_host() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"user_id": "123", "username": "neil"})

    client = SleeperClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.get_user("neil") == {"user_id": "123", "username": "neil"}
    assert str(requests[0].url) == "https://api.sleeper.app/v1/user/neil"


def test_get_players_supports_filtered_query_params() -> None:
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, json={"1": {"full_name": "Example RB"}})

    client = SleeperClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.get_players(position="rb", active=True)["1"]["full_name"] == "Example RB"
    assert seen_url == "https://api.sleeper.app/v1/players/nfl?position=RB&active=true"


def test_stats_use_data_host_and_weekly_path() -> None:
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, json=[])

    client = SleeperClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.get_stats(2025, week=1, position="QB", order_by="pts_ppr") == []
    assert seen_url == (
        "https://api.sleeper.com/stats/nfl/2025/1?"
        "season_type=regular&position%5B%5D=QB&order_by=pts_ppr"
    )


@pytest.mark.parametrize(
    ("call", "expected_url"),
    [
        (
            lambda client: client.get_user_leagues("u1", 2026),
            "https://api.sleeper.app/v1/user/u1/leagues/nfl/2026",
        ),
        (
            lambda client: client.get_user_drafts("u1", 2026),
            "https://api.sleeper.app/v1/user/u1/drafts/nfl/2026",
        ),
        (
            lambda client: client.get_league("l1"),
            "https://api.sleeper.app/v1/league/l1",
        ),
        (
            lambda client: client.get_league_users("l1"),
            "https://api.sleeper.app/v1/league/l1/users",
        ),
        (
            lambda client: client.get_rosters("l1"),
            "https://api.sleeper.app/v1/league/l1/rosters",
        ),
        (
            lambda client: client.get_matchups("l1", 3),
            "https://api.sleeper.app/v1/league/l1/matchups/3",
        ),
        (
            lambda client: client.get_transactions("l1", 3),
            "https://api.sleeper.app/v1/league/l1/transactions/3",
        ),
        (
            lambda client: client.get_traded_picks("l1"),
            "https://api.sleeper.app/v1/league/l1/traded_picks",
        ),
        (
            lambda client: client.get_draft("d1"),
            "https://api.sleeper.app/v1/draft/d1",
        ),
        (
            lambda client: client.get_draft_picks("d1"),
            "https://api.sleeper.app/v1/draft/d1/picks",
        ),
        (
            lambda client: client.get_draft_traded_picks("d1"),
            "https://api.sleeper.app/v1/draft/d1/traded_picks",
        ),
        (
            lambda client: client.get_nfl_state(),
            "https://api.sleeper.app/v1/state/nfl",
        ),
    ],
)
def test_documented_app_endpoint_urls(call, expected_url: str) -> None:
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, json={})

    client = SleeperClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    call(client)

    assert seen_url == expected_url


def test_trending_players_url_and_query_params() -> None:
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, json=[])

    client = SleeperClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.get_trending_players("add", lookback_hours=48, limit=10) == []
    assert seen_url == (
        "https://api.sleeper.app/v1/players/nfl/trending/add?"
        "lookback_hours=48&limit=10"
    )


def test_projections_use_data_host_and_weekly_path() -> None:
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, json=[])

    client = SleeperClient(client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.get_projections(2026, week=2, position="TE", order_by="pts_ppr") == []
    assert seen_url == (
        "https://api.sleeper.com/projections/nfl/2026/2?"
        "season_type=regular&position%5B%5D=TE&order_by=pts_ppr"
    )


def test_load_or_fetch_players_reads_existing_cache_without_api_call(tmp_path) -> None:
    cache_path = tmp_path / "players.json"
    cache_path.write_text('{"1": {"full_name": "Cached Player"}}')

    class Client:
        def get_players(self, **_: object) -> dict[str, dict[str, object]]:
            raise AssertionError("cache hit should not call API")

    assert load_or_fetch_players(Client(), cache_path=cache_path) == {
        "1": {"full_name": "Cached Player"}
    }


def test_load_or_fetch_players_writes_cache_on_miss(tmp_path) -> None:
    cache_path = tmp_path / "nested" / "players.json"
    calls = []

    class Client:
        def get_players(self, **kwargs: object) -> dict[str, dict[str, object]]:
            calls.append(kwargs)
            return {"2": {"full_name": "Fetched Player"}}

    assert load_or_fetch_players(
        Client(),
        cache_path=cache_path,
        position="rb",
        active=True,
    ) == {"2": {"full_name": "Fetched Player"}}
    assert calls == [{"position": "rb", "active": True}]
    assert cache_path.read_text()


def test_raises_api_error_for_failed_response() -> None:
    client = SleeperClient(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500, json={}))
        )
    )

    with pytest.raises(SleeperApiError):
        client.get_league("bad")


def test_client_reads_from_sqlite_cache_on_second_request(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"calls": calls})

    cache = ApiResponseCache(tmp_path / "sleeper.db")
    client = SleeperClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=cache,
    )

    assert client.get_user("neil") == {"calls": 1}
    assert client.get_user("neil") == {"calls": 1}
    assert calls == 1
    client.close()


def test_client_refresh_cache_bypasses_existing_row(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"calls": calls})

    cache_path = tmp_path / "sleeper.db"
    client = SleeperClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=ApiResponseCache(cache_path),
    )
    assert client.get_user("neil") == {"calls": 1}
    client.close()

    refreshing_client = SleeperClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=ApiResponseCache(cache_path),
        refresh_cache=True,
    )
    assert refreshing_client.get_user("neil") == {"calls": 2}
    refreshing_client.close()


def test_api_response_cache_expires_and_clears_rows(tmp_path) -> None:
    cache = ApiResponseCache(tmp_path / "sleeper.db")
    cache.set("fresh", url="https://example.com/fresh", response={"ok": True}, ttl_seconds=60)
    cache.set("expired", url="https://example.com/expired", response={"ok": False}, ttl_seconds=-1)

    assert cache.get("fresh") == {"ok": True}
    assert cache.get("expired") is None
    assert cache.stats()["total_entries"] == 2
    assert cache.clear_expired() == 1
    assert cache.stats()["total_entries"] == 1
    assert cache.clear() == 1
    assert cache.stats()["total_entries"] == 0
    cache.close()


def test_cache_ttl_for_url_uses_endpoint_specific_defaults() -> None:
    assert cache_ttl_for_url("https://api.sleeper.app/v1/state/nfl") == 300
    assert cache_ttl_for_url("https://api.sleeper.app/v1/players/nfl") == 21600
    assert cache_ttl_for_url("https://api.sleeper.com/stats/nfl/2026/1") == 900
    assert cache_ttl_for_url("https://api.sleeper.app/v1/league/1") == 3600
