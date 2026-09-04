import pytest

from sleeper_tooling.client import SleeperClient


pytestmark = pytest.mark.integration


def test_get_nfl_state_from_sleeper_api() -> None:
    with SleeperClient(timeout=10.0) as client:
        state = client.get_nfl_state()

    assert state["season_type"] in {"pre", "regular", "post", "off"}
    assert str(state["season"]).isdigit()
    assert isinstance(state["week"], int)


def test_get_trending_adds_from_sleeper_api() -> None:
    with SleeperClient(timeout=10.0) as client:
        trending = client.get_trending_players("add", lookback_hours=24, limit=5)

    assert isinstance(trending, list)
    assert len(trending) <= 5
    for player in trending:
        assert isinstance(player["player_id"], str)
        assert isinstance(player["count"], int)
