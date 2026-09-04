from __future__ import annotations

import json

from typer.testing import CliRunner

import sleeper_tooling.cli as cli
from sleeper_tooling.cli import app, filter_exact_position, parse_positions


runner = CliRunner()


def test_parse_positions_normalizes_comma_separated_values() -> None:
    assert parse_positions("qb, RB,wr") == ["QB", "RB", "WR"]


def test_filter_exact_position_removes_adjacent_positions() -> None:
    rows = [
        {"name": "Running Back", "position": "RB"},
        {"name": "Fullback", "position": "FB"},
    ]

    assert filter_exact_position(rows, "RB") == [{"name": "Running Back", "position": "RB"}]


def test_best_week_chains_state_and_default_position_projection_calls(monkeypatch) -> None:
    fake_client = FakeSleeperClient()
    monkeypatch.setattr(cli, "SleeperClient", lambda **_: fake_client)

    result = runner.invoke(
        app,
        ["--no-cache", "best-week", "--source", "projections", "--limit", "1", "--output", "json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {
            "name": "Arizona Defense",
            "player_id": "DEF",
            "points": 1,
            "position": "DEF",
            "position_rank": 1,
            "team": "ARI",
        },
        {
            "name": "Kansas Kicker",
            "player_id": "K",
            "points": 1,
            "position": "K",
            "position_rank": 1,
            "team": "KC",
        },
        {
            "name": "Buffalo QB",
            "player_id": "QB",
            "points": 1,
            "position": "QB",
            "position_rank": 1,
            "team": "BUF",
        },
        {
            "name": "Denver RB",
            "player_id": "RB",
            "points": 1,
            "position": "RB",
            "position_rank": 1,
            "team": "DEN",
        },
        {
            "name": "Vegas TE",
            "player_id": "TE",
            "points": 1,
            "position": "TE",
            "position_rank": 1,
            "team": "LV",
        },
        {
            "name": "Detroit WR",
            "player_id": "WR",
            "points": 1,
            "position": "WR",
            "position_rank": 1,
            "team": "DET",
        },
    ]
    assert fake_client.calls == [
        ("state",),
        ("projections", 2026, 1, "QB", "pts_ppr"),
        ("projections", 2026, 1, "RB", "pts_ppr"),
        ("projections", 2026, 1, "WR", "pts_ppr"),
        ("projections", 2026, 1, "TE", "pts_ppr"),
        ("projections", 2026, 1, "K", "pts_ppr"),
        ("projections", 2026, 1, "DEF", "pts_ppr"),
    ]


def test_best_by_team_uses_requested_position_and_filters_adjacent_positions(monkeypatch) -> None:
    fake_client = FakeSleeperClient()
    monkeypatch.setattr(cli, "SleeperClient", lambda **_: fake_client)

    result = runner.invoke(
        app,
        [
            "--no-cache",
            "best-by-team",
            "--season",
            "2026",
            "--week",
            "1",
            "--source",
            "projections",
            "--position",
            "RB",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {
            "name": "Denver RB",
            "player_id": "RB",
            "points": 1,
            "position": "RB",
            "team": "DEN",
            "team_rank": 1,
        }
    ]
    assert fake_client.calls == [("projections", 2026, 1, "RB", "pts_ppr")]


def test_best_week_with_league_id_uses_league_scoring_settings(monkeypatch) -> None:
    fake_client = FakeSleeperClient()
    monkeypatch.setattr(cli, "SleeperClient", lambda **_: fake_client)

    result = runner.invoke(
        app,
        [
            "--no-cache",
            "best-week",
            "--season",
            "2026",
            "--week",
            "1",
            "--league-id",
            "league-1",
            "--source",
            "projections",
            "--positions",
            "RB",
            "--limit",
            "1",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {
            "name": "Denver RB",
            "player_id": "RB",
            "points": 30,
            "position": "RB",
            "position_rank": 1,
            "scoring_rules_matched": 1,
            "sleeper_points": 1,
            "team": "DEN",
        }
    ]
    assert fake_client.calls == [
        ("league", "league-1"),
        ("projections", 2026, 1, "RB", "pts_ppr"),
    ]


def test_weekly_briefing_chains_leaders_player_cache_and_trends(monkeypatch, tmp_path) -> None:
    fake_client = FakeSleeperClient()
    monkeypatch.setattr(cli, "SleeperClient", lambda **_: fake_client)

    result = runner.invoke(
        app,
        [
            "--no-cache",
            "weekly-briefing",
            "--positions",
            "QB,RB",
            "--leader-limit",
            "1",
            "--trend-limit",
            "1",
            "--players-cache",
            str(tmp_path / "players.json"),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["season"] == 2026
    assert data["week"] == 1
    assert data["leader_source"] == "projections"
    assert data["leaders"] == [
        {
            "name": "Buffalo QB",
            "player_id": "QB",
            "points": 1,
            "position": "QB",
            "position_rank": 1,
            "team": "BUF",
        },
        {
            "name": "Denver RB",
            "player_id": "RB",
            "points": 1,
            "position": "RB",
            "position_rank": 1,
            "team": "DEN",
        },
    ]
    assert data["trending_adds"] == [
        {
            "count": 99,
            "injury_status": "",
            "name": "Trending RB",
            "player_id": "trend-rb",
            "position": "RB",
            "status": "Active",
            "team": "DEN",
            "trend_type": "add",
        }
    ]
    assert fake_client.calls == [
        ("state",),
        ("players", None, None),
        ("projections", 2026, 1, "QB", "pts_ppr"),
        ("projections", 2026, 1, "RB", "pts_ppr"),
        ("trending", "add", 24, 1),
    ]


def test_weekly_briefing_with_league_id_reports_scoring_source(monkeypatch, tmp_path) -> None:
    fake_client = FakeSleeperClient()
    monkeypatch.setattr(cli, "SleeperClient", lambda **_: fake_client)

    result = runner.invoke(
        app,
        [
            "--no-cache",
            "weekly-briefing",
            "--league-id",
            "league-1",
            "--positions",
            "RB",
            "--leader-limit",
            "1",
            "--trend-limit",
            "1",
            "--players-cache",
            str(tmp_path / "players.json"),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["scoring_source"] == "league-1"
    assert data["leaders"][0]["points"] == 30
    assert data["leaders"][0]["sleeper_points"] == 1
    assert fake_client.calls == [
        ("state",),
        ("league", "league-1"),
        ("players", None, None),
        ("projections", 2026, 1, "RB", "pts_ppr"),
        ("trending", "add", 24, 1),
    ]


def test_trending_enrich_joins_player_metadata(monkeypatch, tmp_path) -> None:
    fake_client = FakeSleeperClient()
    monkeypatch.setattr(cli, "SleeperClient", lambda **_: fake_client)

    result = runner.invoke(
        app,
        [
            "--no-cache",
            "trending",
            "add",
            "--limit",
            "1",
            "--enrich",
            "--players-cache",
            str(tmp_path / "players.json"),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {
            "count": 99,
            "injury_status": "",
            "name": "Trending RB",
            "player_id": "trend-rb",
            "position": "RB",
            "status": "Active",
            "team": "DEN",
            "trend_type": "add",
        }
    ]


def test_waiver_watch_chains_trends_projections_rosters_and_player_cache(monkeypatch, tmp_path) -> None:
    fake_client = FakeSleeperClient()
    monkeypatch.setattr(cli, "SleeperClient", lambda **_: fake_client)

    result = runner.invoke(
        app,
        [
            "--no-cache",
            "waiver-watch",
            "league-1",
            "--season",
            "2026",
            "--week",
            "1",
            "--positions",
            "RB",
            "--trend-limit",
            "2",
            "--limit",
            "1",
            "--players-cache",
            str(tmp_path / "players.json"),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {
            "injury_status": "",
            "name": "Trending RB",
            "player_id": "trend-rb",
            "position": "RB",
            "projected_points": 30,
            "sleeper_projected_points": 1,
            "status": "Active",
            "team": "DEN",
            "trend_count": 99,
            "trend_type": "add",
        }
    ]
    assert fake_client.calls == [
        ("league", "league-1"),
        ("projections", 2026, 1, "RB", "pts_ppr"),
        ("trending", "add", 24, 2),
        ("players", None, None),
        ("rosters", "league-1"),
    ]


def test_injury_watch_chains_league_users_rosters_and_players(monkeypatch, tmp_path) -> None:
    fake_client = FakeSleeperClient()
    monkeypatch.setattr(cli, "SleeperClient", lambda **_: fake_client)

    result = runner.invoke(
        app,
        [
            "--no-cache",
            "injury-watch",
            "league-1",
            "--players-cache",
            str(tmp_path / "players.json"),
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == [
        {
            "injury_status": "Questionable",
            "name": "Hurt RB",
            "owner_id": "u1",
            "player_id": "hurt-rb",
            "position": "RB",
            "roster_id": 1,
            "status": "Active",
            "team": "DEN",
            "team_name": "Mile High",
        }
    ]
    assert fake_client.calls == [
        ("users", "league-1"),
        ("rosters", "league-1"),
        ("players", None, None),
    ]


def test_cache_info_and_clear_use_configured_sqlite_db(tmp_path) -> None:
    db_path = tmp_path / "sleeper.db"
    cache = cli.ApiResponseCache(db_path)
    cache.set("key", url="https://example.com", response={"ok": True}, ttl_seconds=60)
    cache.close()

    info_result = runner.invoke(app, ["--cache-db", str(db_path), "cache-info"])
    assert info_result.exit_code == 0
    assert json.loads(info_result.stdout)["total_entries"] == 1

    clear_result = runner.invoke(app, ["--cache-db", str(db_path), "cache-clear"])
    assert clear_result.exit_code == 0
    assert json.loads(clear_result.stdout)["deleted"] == 1
    assert json.loads(clear_result.stdout)["total_entries"] == 0


class FakeSleeperClient:
    team_by_position = {
        "QB": "BUF",
        "RB": "DEN",
        "WR": "DET",
        "TE": "LV",
        "K": "KC",
        "DEF": "ARI",
    }

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def __enter__(self) -> "FakeSleeperClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def get_nfl_state(self) -> dict[str, object]:
        self.calls.append(("state",))
        return {"season": "2026", "week": 1}

    def get_league(self, league_id: str) -> dict[str, object]:
        self.calls.append(("league", league_id))
        return {"scoring_settings": {"custom_score": 10}}

    def get_players(self, *, position=None, active=None, sport="nfl") -> dict[str, dict[str, object]]:
        self.calls.append(("players", position, active))
        return {
            "trend-rb": {
                "full_name": "Trending RB",
                "team": "DEN",
                "position": "RB",
                "status": "Active",
                "injury_status": None,
            },
            "hurt-rb": {
                "full_name": "Hurt RB",
                "team": "DEN",
                "position": "RB",
                "status": "Active",
                "injury_status": "Questionable",
            }
        }

    def get_trending_players(
        self,
        trend_type,
        *,
        sport="nfl",
        lookback_hours=24,
        limit=25,
    ) -> list[dict[str, object]]:
        self.calls.append(("trending", trend_type, lookback_hours, limit))
        return [
            {"player_id": "trend-rb", "count": 99},
            {"player_id": "rostered-rb", "count": 88},
        ][:limit]

    def get_rosters(self, league_id: str) -> list[dict[str, object]]:
        self.calls.append(("rosters", league_id))
        return [{"roster_id": 1, "owner_id": "u1", "players": ["hurt-rb", "rostered-rb"]}]

    def get_league_users(self, league_id: str) -> list[dict[str, object]]:
        self.calls.append(("users", league_id))
        return [
            {
                "user_id": "u1",
                "display_name": "Neil",
                "metadata": {"team_name": "Mile High"},
            }
        ]

    def get_projections(
        self,
        season,
        *,
        week=None,
        sport="nfl",
        season_type="regular",
        position=None,
        order_by=None,
    ) -> list[dict[str, object]]:
        self.calls.append(("projections", season, week, position, order_by))
        return self._rows_for_position(position)

    def get_stats(
        self,
        season,
        *,
        week=None,
        sport="nfl",
        season_type="regular",
        position=None,
        order_by=None,
    ) -> list[dict[str, object]]:
        self.calls.append(("stats", season, week, position, order_by))
        return self._rows_for_position(position)

    def _rows_for_position(self, position: str | None) -> list[dict[str, object]]:
        position = position or "RB"
        team = self.team_by_position[position]
        rows: list[dict[str, object]] = [
            {
                "player_id": position,
                "player": {
                    "full_name": _player_name(team, position),
                    "team": team,
                    "position": position,
                },
                "stats": {"pts_ppr": 1, "custom_score": 3},
            }
        ]
        if position == "RB":
            rows.append(
                {
                    "player_id": "FB",
                    "player": {
                        "full_name": "Filtered Fullback",
                        "team": "DEN",
                        "position": "FB",
                    },
                    "stats": {"pts_ppr": 50},
                }
            )
            rows.append(
                {
                    "player_id": "trend-rb",
                    "player": {
                        "full_name": "Trending RB",
                        "team": "DEN",
                        "position": "RB",
                    },
                    "stats": {"pts_ppr": 1, "custom_score": 3},
                }
            )
        return rows


def _team_name(team: str) -> str:
    return {
        "ARI": "Arizona",
        "BUF": "Buffalo",
        "DEN": "Denver",
        "DET": "Detroit",
        "KC": "Kansas",
        "LV": "Vegas",
    }[team]


def _player_name(team: str, position: str) -> str:
    suffix = {"DEF": "Defense", "K": "Kicker"}.get(position, position)
    return f"{_team_name(team)} {suffix}"
