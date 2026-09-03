from __future__ import annotations

from pathlib import Path

import pytest

from sleeper_tooling.mcp_tools import FantasyToolRunner


def test_free_agent_watch_returns_unrostered_projected_players(tmp_path) -> None:
    fake_client = FakeMcpClient()
    runner = FantasyToolRunner(
        client_factory=lambda: fake_client,
        players_cache=tmp_path / "players.json",
    )

    rows = runner.free_agent_watch(
        league_id="league-1",
        season=2026,
        week=1,
        positions="RB",
        limit=1,
    )

    assert rows == [
        {
            "league_id": "league-1",
            "season": 2026,
            "week": 1,
            "player_id": "free-rb",
            "name": "Free RB",
            "team": "DEN",
            "position": "RB",
            "projected_points": 20,
            "sleeper_projected_points": 2,
            "status": "Active",
            "injury_status": "",
        }
    ]


def test_tools_use_default_league_id_when_argument_is_omitted(tmp_path) -> None:
    fake_client = FakeMcpClient()
    runner = FantasyToolRunner(
        client_factory=lambda: fake_client,
        players_cache=tmp_path / "players.json",
        default_league_id="default-league",
    )

    rows = runner.free_agent_watch(season=2026, week=1, positions="RB", limit=1)

    assert rows[0]["league_id"] == "default-league"
    assert fake_client.league_ids == ["default-league"]


def test_explicit_league_id_overrides_default_league_id(tmp_path) -> None:
    fake_client = FakeMcpClient()
    runner = FantasyToolRunner(
        client_factory=lambda: fake_client,
        players_cache=tmp_path / "players.json",
        default_league_id="default-league",
    )

    rows = runner.free_agent_watch(
        league_id="explicit-league",
        season=2026,
        week=1,
        positions="RB",
        limit=1,
    )

    assert rows[0]["league_id"] == "explicit-league"
    assert fake_client.league_ids == ["explicit-league"]


def test_missing_required_league_context_raises_clear_error(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeMcpClient(),
        players_cache=tmp_path / "players.json",
    )

    with pytest.raises(ValueError, match="SLEEPER_DEFAULT_LEAGUE_ID"):
        runner.injury_watch()


def test_weekly_performance_backtest_returns_leaders_and_movers(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeBacktestClient(),
        players_cache=tmp_path / "players.json",
    )

    report = runner.weekly_performance_backtest(
        season=2026,
        start_week=1,
        weeks=2,
        positions="RB",
        limit=2,
        movement_limit=2,
    )

    assert report["weeks"] == [1, 2]
    assert report["weekly_leaders"][0]["leaders"][0]["name"] == "Stable RB"
    assert report["weekly_leaders"][1]["leaders"][0]["name"] == "Rising RB"
    comparison = report["week_over_week"][0]
    assert comparison["previous_week"] == 1
    assert comparison["current_week"] == 2
    assert comparison["top_risers"][0] == {
        "player_id": "rising-rb",
        "name": "Rising RB",
        "team": "DEN",
        "position": "RB",
        "previous_points": 6.0,
        "current_points": 14.0,
        "points_delta": 8.0,
        "previous_rank": 2,
        "current_rank": 1,
        "rank_delta": 1,
    }
    assert comparison["top_fallers"][0]["player_id"] == "stable-rb"
    assert comparison["appeared"][0]["player_id"] == "new-rb"


def test_weekly_performance_backtest_rejects_invalid_source(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeBacktestClient(),
        players_cache=tmp_path / "players.json",
    )

    with pytest.raises(ValueError, match="source must be"):
        runner.weekly_performance_backtest(
            season=2026,
            start_week=1,
            source="live",  # type: ignore[arg-type]
        )


def test_waiver_wire_watch_returns_actionable_ranked_candidates(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeMcpClient(),
        players_cache=tmp_path / "players.json",
    )

    report = runner.waiver_wire_watch(
        league_id="league-1",
        season=2026,
        week=2,
        positions="RB",
        limit=1,
        recent_weeks=1,
    )

    assert report["league_id"] == "league-1"
    candidate = report["candidates"][0]
    assert candidate["player_id"] == "free-rb"
    assert candidate["league_id"] == "league-1"
    assert candidate["season"] == 2026
    assert candidate["week"] == 2
    assert candidate["projected_points"] == 20
    assert candidate["drop_trend_count"] == 5
    assert candidate["net_trend_count"] == 45
    assert candidate["recent_average_points"] == 10
    assert candidate["watch_score"] == 30.45


def test_opponent_watch_returns_matchup_context(tmp_path) -> None:
    fake_client = FakeMcpClient()
    runner = FantasyToolRunner(
        client_factory=lambda: fake_client,
        players_cache=tmp_path / "players.json",
    )

    report = runner.opponent_watch(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
    )

    assert report["opponent_found"] is True
    assert report["league_id"] == "league-1"
    assert report["roster_id"] == 1
    assert report["season"] == 2026
    assert report["opponent_team_name"] == "Opponent"
    assert report["opponent_projected_starter_points"] == 10
    assert report["opponent_injuries"][0]["player_id"] == "hurt-wr"


def test_opponent_watch_uses_default_league_and_roster_ids(tmp_path) -> None:
    fake_client = FakeMcpClient()
    runner = FantasyToolRunner(
        client_factory=lambda: fake_client,
        players_cache=tmp_path / "players.json",
        default_league_id="default-league",
        default_roster_id=1,
    )

    report = runner.opponent_watch(season=2026, week=1)

    assert report["league_id"] == "default-league"
    assert report["roster_id"] == 1
    assert report["opponent_found"] is True


def test_missing_required_roster_context_raises_clear_error(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeMcpClient(),
        players_cache=tmp_path / "players.json",
        default_league_id="league-1",
    )

    with pytest.raises(ValueError, match="SLEEPER_DEFAULT_ROSTER_ID"):
        runner.opponent_watch(season=2026, week=1)


def test_league_team_watch_summarizes_completed_transactions(tmp_path) -> None:
    fake_client = FakeMcpClient()
    runner = FantasyToolRunner(
        client_factory=lambda: fake_client,
        players_cache=tmp_path / "players.json",
    )

    rows = runner.league_team_watch(league_id="league-1", week=1)

    assert rows == [
        {
            "league_id": "league-1",
            "week": 1,
            "transaction_id": "txn-1",
            "type": "waiver",
            "status": "complete",
            "created": 100,
            "roster_ids": [2],
            "adds": [
                {
                    "player_id": "free-rb",
                    "name": "Free RB",
                    "team": "DEN",
                    "position": "RB",
                    "roster_id": 2,
                    "team_name": "Opponent",
                }
            ],
            "drops": [],
            "adds_summary": "Free RB",
            "drops_summary": "",
        }
    ]


def test_player_card_returns_chart_ready_points(tmp_path) -> None:
    fake_client = FakeMcpClient()
    runner = FantasyToolRunner(
        client_factory=lambda: fake_client,
        players_cache=tmp_path / "players.json",
    )

    report = runner.player_card(
        player_id="free-rb",
        league_id="league-1",
        season=2026,
        week=2,
        weeks_back=2,
    )

    assert report["name"] == "Free RB"
    assert report["league_id"] == "league-1"
    assert report["chart_data"]["weekly_points"] == [
        {"week": 1, "actual_points": 10, "projected_points": 20},
        {"week": 2, "actual_points": 10, "projected_points": 20},
    ]


class FakeMcpClient:
    def __init__(self) -> None:
        self.league_ids: list[str] = []

    def __enter__(self) -> "FakeMcpClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_nfl_state(self) -> dict[str, object]:
        return {"season": "2026", "week": 1}

    def get_league(self, league_id: str) -> dict[str, object]:
        self.league_ids.append(league_id)
        return {"scoring_settings": {"custom_score": 10}}

    def get_rosters(self, league_id: str) -> list[dict[str, object]]:
        return [
            {"roster_id": 1, "owner_id": "u1", "players": ["rostered-rb"]},
            {"roster_id": 2, "owner_id": "u2", "players": ["hurt-wr"]},
        ]

    def get_league_users(self, league_id: str) -> list[dict[str, object]]:
        return [
            {"user_id": "u1", "display_name": "Me", "metadata": {"team_name": "Me"}},
            {
                "user_id": "u2",
                "display_name": "Opponent",
                "metadata": {"team_name": "Opponent"},
            },
        ]

    def get_matchups(self, league_id: str, week: int) -> list[dict[str, object]]:
        return [
            {"roster_id": 1, "matchup_id": 10, "points": 0, "starters": ["rostered-rb"]},
            {"roster_id": 2, "matchup_id": 10, "points": 0, "starters": ["hurt-wr"]},
        ]

    def get_transactions(self, league_id: str, week: int) -> list[dict[str, object]]:
        return [
            {
                "transaction_id": "txn-1",
                "type": "waiver",
                "status": "complete",
                "created": 100,
                "roster_ids": [2],
                "adds": {"free-rb": 2},
                "drops": {},
            },
            {"transaction_id": "txn-2", "status": "failed"},
        ]

    def get_players(self, *, position=None, active=None, sport="nfl") -> dict[str, dict[str, object]]:
        return {
            "free-rb": {
                "full_name": "Free RB",
                "team": "DEN",
                "position": "RB",
                "status": "Active",
                "injury_status": "",
            },
            "rostered-rb": {
                "full_name": "Rostered RB",
                "team": "KC",
                "position": "RB",
                "status": "Active",
                "injury_status": "",
            },
            "hurt-wr": {
                "full_name": "Hurt WR",
                "team": "LV",
                "position": "WR",
                "status": "Active",
                "injury_status": "Questionable",
            },
        }

    def get_trending_players(self, trend_type, *, sport="nfl", lookback_hours=24, limit=25):
        if trend_type == "drop":
            return [{"player_id": "free-rb", "count": 5}]
        return [{"player_id": "free-rb", "count": 50}]

    def get_projections(self, season, *, week=None, sport="nfl", season_type="regular", position=None, order_by=None):
        return self._rows(position, projected=True)

    def get_stats(self, season, *, week=None, sport="nfl", season_type="regular", position=None, order_by=None):
        return self._rows(position, projected=False)

    def _rows(self, position: str | None, *, projected: bool) -> list[dict[str, object]]:
        rows = []
        if position == "RB":
            rows.extend(
                [
                    self._row("free-rb", "Free RB", "DEN", "RB", 2 if projected else 1),
                    self._row("rostered-rb", "Rostered RB", "KC", "RB", 3),
                ]
            )
        if position == "WR":
            rows.append(self._row("hurt-wr", "Hurt WR", "LV", "WR", 1))
        return rows

    def _row(
        self,
        player_id: str,
        name: str,
        team: str,
        position: str,
        custom_score: int,
    ) -> dict[str, object]:
        return {
            "player_id": player_id,
            "player": {"full_name": name, "team": team, "position": position},
            "stats": {"custom_score": custom_score, "pts_ppr": custom_score},
        }


class FakeBacktestClient:
    def __enter__(self) -> "FakeBacktestClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_nfl_state(self) -> dict[str, object]:
        return {"season": "2026", "week": 2}

    def get_stats(self, season, *, week=None, sport="nfl", season_type="regular", position=None, order_by=None):
        if position != "RB":
            return []
        if week == 1:
            return [
                self._row("stable-rb", "Stable RB", 10),
                self._row("rising-rb", "Rising RB", 6),
            ]
        return [
            self._row("rising-rb", "Rising RB", 14),
            self._row("new-rb", "New RB", 8),
            self._row("stable-rb", "Stable RB", 5),
        ]

    def get_projections(self, season, *, week=None, sport="nfl", season_type="regular", position=None, order_by=None):
        return self.get_stats(
            season,
            week=week,
            sport=sport,
            season_type=season_type,
            position=position,
            order_by=order_by,
        )

    def _row(self, player_id: str, name: str, points: int) -> dict[str, object]:
        return {
            "player_id": player_id,
            "player": {"full_name": name, "team": "DEN", "position": "RB"},
            "stats": {"pts_ppr": points},
        }
