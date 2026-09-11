from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import sleeper_tooling.mcp_tools as mcp_tools
from sleeper_tooling.db import SleeperNormalizedRepository
from sleeper_tooling.mcp_tools import FantasyToolRunner


@pytest.fixture(autouse=True)
def clear_default_context_env(monkeypatch) -> None:
    monkeypatch.delenv("SLEEPER_DEFAULT_LEAGUE_ID", raising=False)
    monkeypatch.delenv("SLEEPER_DEFAULT_ROSTER_ID", raising=False)
    monkeypatch.delenv("SLEEPER_DEFAULT_OWNER_ID", raising=False)


def test_waiver_tools_default_to_all_standard_positions() -> None:
    assert (
        inspect.signature(FantasyToolRunner.waiver_wire_watch)
        .parameters["positions"]
        .default
        == mcp_tools.DEFAULT_POSITIONS
    )
    assert (
        inspect.signature(FantasyToolRunner.free_agent_watch)
        .parameters["positions"]
        .default
        == mcp_tools.DEFAULT_POSITIONS
    )


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

    assert rows[0]["league_id"] == "league-1"
    assert rows[0]["player_id"] == "free-rb"
    assert rows[0]["name"] == "Free RB"
    assert rows[0]["market_type"] == "free_agent"
    assert rows[0]["acquisition_action"] == "add_now"
    assert "faab_bid_pct" not in rows[0]
    assert "depth_chart_order" in rows[0]
    assert "source_metadata" in rows[0]
    assert rows[0]["source_metadata"]["player_context"] == [
        "sleeper_players",
        "sleeper_projections",
    ]


def test_mcp_resolve_season_week_defaults_season_to_current_year(monkeypatch) -> None:
    fake_client = FakeMcpClient()
    monkeypatch.setattr(mcp_tools, "current_season_year", lambda: 2030)

    assert mcp_tools.resolve_season_week(fake_client, None, 4) == (2030, 4)


def test_resolve_league_context_returns_env_and_cloudflare_vars(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeMcpClient(),
        players_cache=tmp_path / "players.json",
    )

    context = runner.resolve_league_context(
        league_ref="https://sleeper.com/leagues/1389328071634460672/matchup",
        user_ref="Me",
    )

    assert context["league_id"] == "1389328071634460672"
    assert context["roster_id"] == 1
    assert context["cloudflare_vars"] == {
        "SLEEPER_DEFAULT_LEAGUE_ID": "1389328071634460672",
        "SLEEPER_DEFAULT_ROSTER_ID": "1",
        "SLEEPER_DEFAULT_OWNER_ID": "u1",
    }
    assert "SLEEPER_DEFAULT_ROSTER_ID=1" in context["env_text"]


def test_decision_data_status_does_not_call_sleeper_client() -> None:
    class FailingClient:
        def __enter__(self):
            raise AssertionError("decision_data_status must not call Sleeper")

    runner = FantasyToolRunner(
        client_factory=lambda: FailingClient(),
        trend_repository=FakeStatusRepository(
            {
                "row_count": 8,
                "last_synced_at": 9999999999,
                "latest_stats_week": 2,
                "latest_projections_week": 3,
                "sources": ["stats", "projections"],
            }
        ),
    )

    status = runner.decision_data_status(season=2026, max_age_hours=1)

    assert status["status"] == "fresh"
    assert status["fresh"] is True
    assert status["row_count"] == 8
    assert status["latest_stats_week"] == 2


def test_decision_data_status_reports_stale_and_missing() -> None:
    stale_runner = FantasyToolRunner(
        trend_repository=FakeStatusRepository({"row_count": 8, "last_synced_at": 1})
    )
    missing_runner = FantasyToolRunner(
        trend_repository=FakeStatusRepository({"row_count": 0, "last_synced_at": 1})
    )

    assert stale_runner.decision_data_status(max_age_hours=1)["status"] == "stale"
    assert missing_runner.decision_data_status(max_age_hours=1)["status"] == "missing"


def test_sync_decision_data_delegates_to_configured_sync_service() -> None:
    service = FakeSyncService()
    runner = FantasyToolRunner(
        sync_service=service,
        default_league_id="default-league",
    )

    report = runner.sync_decision_data(season=2026, week=1, force=True)

    assert report == {
        "synced": True,
        "league_id": "default-league",
        "season": 2026,
        "week": 1,
        "force": True,
    }
    assert service.calls == [
        {
            "league_id": "default-league",
            "season": 2026,
            "week": 1,
            "force": True,
        }
    ]


def test_player_stat_trends_returns_compact_graph_shape() -> None:
    repo = FakeTrendRepository()
    runner = FantasyToolRunner(trend_repository=repo)

    report = runner.player_stat_trends(
        season=2026,
        player_id="rb-1",
        stat_key="rush_att",
        start_week=1,
        end_week=2,
    )

    assert report["shape"] == [
        "season",
        "week",
        "player_id",
        "name",
        "team",
        "position",
        "stat_key",
        "stat_value",
    ]
    assert report["rows"] == [
        {
            "season": 2026,
            "week": 1,
            "player_id": "rb-1",
            "name": "Runner One",
            "team": "DEN",
            "position": "RB",
            "stat_key": "rush_att",
            "stat_value": 10,
        },
        {
            "season": 2026,
            "week": 2,
            "player_id": "rb-1",
            "name": "Runner One",
            "team": "DEN",
            "position": "RB",
            "stat_key": "rush_att",
            "stat_value": 12,
        },
    ]
    assert repo.calls[0]["source"] == "stats"


def test_position_stat_leaders_returns_graph_friendly_leaders() -> None:
    runner = FantasyToolRunner(trend_repository=FakeTrendRepository())

    report = runner.position_stat_leaders(
        season=2026,
        week=2,
        position="rb",
        stat_key="rush_att",
        limit=1,
    )

    assert report["position"] == "RB"
    assert report["leaders"] == [
        {
            "position_rank": 1,
            "season": 2026,
            "week": 2,
            "player_id": "rb-2",
            "name": "Runner Two",
            "team": "KC",
            "position": "RB",
            "stat_key": "rush_att",
            "stat_value": 18,
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


def test_my_lineup_returns_slots_starters_bench_and_projections(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeLineupClient(),
        players_cache=tmp_path / "players.json",
    )

    report = runner.my_lineup(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="RB,WR",
    )

    assert report["league_id"] == "league-1"
    assert report["roster_id"] == 1
    assert report["team_name"] == "Me"
    assert report["roster_slots"] == ["RB", "FLEX"]
    assert report["current_total"] == 4
    assert report["projected_total"] == 67
    assert report["projected_starter_total"] == 24
    assert report["projected_starter_points"] == 24
    assert [row["player_id"] for row in report["lineup_table"]] == [
        "starter-rb",
        "bench-wr",
        "bench-rb",
        "drop-rb",
        "ir-rb",
    ]
    assert report["lineup_table"][0]["actual_points"] == 2
    assert report["lineup_table"][0]["status"] == "Active"
    assert [row["slot"] for row in report["starters"]] == ["RB", "FLEX"]
    assert [row["player_id"] for row in report["starters"]] == [
        "starter-rb",
        "bench-wr",
    ]
    assert [row["player_id"] for row in report["bench"]] == ["bench-rb", "drop-rb"]
    assert report["bench"][0]["projected_points"] == 20
    assert report["active_bench_count"] == 2
    assert report["reserve_count"] == 1
    assert report["reserve"][0]["player_id"] == "ir-rb"
    assert report["reserve"][0]["slot"] == "IR"
    assert report["reserve"][0]["lineup_status"] == "reserve"
    assert report["reserve"][0]["active_roster_spot"] is False
    assert report["reserve"][0]["stash_value"] is True
    assert report["reserve"][0]["depth_chart_order"] == 2


def test_my_lineup_uses_fresh_normalized_db_without_sleeper_client(tmp_path) -> None:
    repo = build_normalized_decision_fixture(tmp_path)
    runner = FantasyToolRunner(
        client_factory=lambda: FailingMcpClient(),
        decision_repository=repo,
    )

    report = runner.my_lineup(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="RB,WR",
    )

    assert report["data_source"] == "normalized_db"
    assert report["fallback_used"] is False
    assert report["sync_recommended"] is False
    assert report["freshness"]["status"] == "fresh"
    assert report["team_name"] == "Me"
    assert report["projected_starter_total"] == 24
    assert [row["player_id"] for row in report["lineup_table"]] == [
        "starter-rb",
        "bench-wr",
        "drop-rb",
        "ir-rb",
    ]
    assert report["reserve"][0]["active_roster_spot"] is False
    repo.close()


def test_my_lineup_uses_default_normalized_repository_from_cache_db(tmp_path) -> None:
    cache_db = tmp_path / "default-wiring.db"
    repo = build_normalized_decision_fixture(tmp_path, db_path=cache_db)
    repo.close()
    runner = FantasyToolRunner(
        client_factory=lambda: FailingMcpClient(),
        cache_db=cache_db,
    )

    report = runner.my_lineup(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="RB,WR",
    )

    assert report["data_source"] == "normalized_db"
    assert report["team_name"] == "Me"
    assert report["projected_starter_total"] == 24
    runner._decision_repository.close()


def test_my_lineup_empty_normalized_db_falls_back_with_warning(tmp_path) -> None:
    repo = SleeperNormalizedRepository(tmp_path / "empty.db")
    runner = FantasyToolRunner(
        client_factory=lambda: FakeLineupClient(),
        decision_repository=repo,
        players_cache=tmp_path / "players.json",
    )

    report = runner.my_lineup(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="RB,WR",
    )

    assert report["data_source"] == "sleeper_fallback"
    assert report["fallback_used"] is True
    assert report["sync_recommended"] is True
    assert report["freshness"]["status"] == "missing"
    assert "league_settings" in report["freshness"]["missing_inputs"]
    assert report["projected_starter_total"] == 24
    repo.close()


def test_my_lineup_stale_normalized_db_falls_back_with_warning(tmp_path) -> None:
    repo = build_normalized_decision_fixture(tmp_path)
    mark_normalized_fixture_stale(repo)
    sync_run_id = repo.create_sync_run(
        target="normalized_sleeper_data",
        league_id="league-1",
        status="running",
    )
    repo.finish_sync_run(sync_run_id, status="failed", error_text="boom")
    runner = FantasyToolRunner(
        client_factory=lambda: FakeLineupClient(),
        decision_repository=repo,
        players_cache=tmp_path / "players.json",
    )

    report = runner.my_lineup(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="RB,WR",
    )

    assert report["data_source"] == "sleeper_fallback"
    assert report["fallback_used"] is True
    assert report["sync_recommended"] is True
    assert report["freshness"]["status"] == "stale"
    assert report["freshness"]["latest_sync"]["status"] == "failed"
    assert "normalized decision data is stale" in report["freshness"]["warnings"]
    assert report["projected_starter_total"] == 24
    repo.close()


def test_lineup_recommendations_compares_lineup_and_available_players(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeLineupClient(),
        players_cache=tmp_path / "players.json",
    )

    report = runner.lineup_recommendations(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="RB,WR",
        min_delta=1,
        limit=3,
    )

    assert report["current_lineup"]["projected_starter_points"] == 24
    assert report["start_sit"][0] == {
        "action": "start",
        "slot": "RB",
        "start_player_id": "bench-rb",
        "start_name": "Bench RB",
        "start_position": "RB",
        "start_team": "DEN",
        "start_projected_points": 20.0,
        "sit_player_id": "starter-rb",
        "sit_name": "Starter RB",
        "sit_position": "RB",
        "sit_team": "KC",
        "sit_projected_points": 10.0,
        "projected_gain": 10.0,
        "evidence": [
            "Bench RB is eligible for RB",
            "recommendation is based on projected point delta",
        ],
    }
    waiver_pick = report["waiver_comparisons"][0]
    assert waiver_pick["add_player_id"] == "free-rb"
    assert waiver_pick["drop_player_id"] == "drop-rb"
    assert waiver_pick["projected_gain_over_drop"] == 25
    assert waiver_pick["drop_lineup_status"] == "bench"
    assert waiver_pick["drop_reason"] == "lowest risk active roster cut with comparable position coverage"
    assert waiver_pick["market_type"] == "waiver"
    assert waiver_pick["acquisition_action"] == "submit_waiver_claim"
    assert waiver_pick["faab_bid_pct"] == 14
    assert waiver_pick["faab_tier"] == "aggressive"
    assert "projects 25.00 points above" in waiver_pick["faab_reasoning"]
    assert {
        "player_id": "ir-rb",
        "name": "IR RB",
        "position": "RB",
        "reason": "reserve/IR stash does not consume an active bench spot",
    } in waiver_pick["rejected_drop_reasoning"]
    watch = report["watchlist"][0]
    assert watch["player_id"] == "free-rb"
    assert watch["net_trend_count"] == 2400
    assert watch["rostered_percent"] == 55.0


def test_waiver_wire_by_position_groups_options_with_gain_and_faab(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeLineupClient(),
        players_cache=tmp_path / "players.json",
    )

    report = runner.waiver_wire_by_position(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="RB,WR",
        per_position_limit=1,
    )

    assert report["positions"] == ["RB", "WR"]
    rb_pick = report["by_position"]["RB"][0]
    assert rb_pick["add_name"] == "Free RB"
    assert rb_pick["drop_name"] == "Drop RB"
    assert rb_pick["drop_lineup_status"] == "bench"
    assert rb_pick["projected_gain_over_drop"] == 25
    assert rb_pick["faab_tier"] == "aggressive"
    assert rb_pick["faab_bid_pct"] == 14
    assert rb_pick["add_trend_count"] == 2500
    wr_pick = report["by_position"]["WR"][0]
    assert wr_pick["add_name"] == "Watch WR"
    assert wr_pick["drop_name"] == "Drop RB"
    assert wr_pick["drop_lineup_status"] == "bench"
    assert wr_pick["projected_gain_over_drop"] == 10
    assert wr_pick["market_type"] == "unknown"
    assert wr_pick["acquisition_action"] == "watch"
    assert "faab_tier" not in wr_pick


def test_waiver_wire_by_position_uses_normalized_db(tmp_path) -> None:
    repo = build_normalized_decision_fixture(tmp_path)
    runner = FantasyToolRunner(
        client_factory=lambda: FailingMcpClient(),
        decision_repository=repo,
    )

    report = runner.waiver_wire_by_position(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="RB,WR",
        per_position_limit=1,
    )

    assert report["data_source"] == "normalized_db"
    assert report["fallback_used"] is False
    rb_pick = report["by_position"]["RB"][0]
    assert rb_pick["add_name"] == "Free RB"
    assert rb_pick["drop_name"] == "Drop RB"
    assert rb_pick["projected_gain_over_drop"] == 25
    assert rb_pick["market_type"] == "waiver"
    assert rb_pick["faab_tier"] == "aggressive"
    wr_pick = report["by_position"]["WR"][0]
    assert wr_pick["add_name"] == "Free WR"
    assert wr_pick["projected_gain_over_drop"] == 10
    repo.close()


def test_trade_opportunities_includes_every_opponent_with_offer_angles(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeTradeClient(),
        players_cache=tmp_path / "players.json",
    )

    report = runner.trade_opportunities(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="QB,RB,WR,TE",
        targets_per_team=3,
        offers_per_team=2,
    )

    assert [team["team_name"] for team in report["teams"]] == [
        "WR Rich",
        "No Fit",
    ]
    wr_rich = report["teams"][0]
    assert "TE" in {need["position"] for need in wr_rich["needs"]}
    assert wr_rich["targets"][0]["name"] == "Target WR"
    assert wr_rich["offer_angles"][0]["ask_for"]["name"] == "Target WR"
    assert wr_rich["offer_angles"][0]["offer"][0]["name"] == "Bench TE"
    assert wr_rich["offer_angles"][0]["projected_lineup_gain"] == 6
    assert wr_rich["offer_angles"][0]["my_gain"] == 6
    assert "TE" in wr_rich["offer_angles"][0]["opponent_need_matched"]
    assert wr_rich["offer_angles"][0]["trade_score"] > 0
    assert wr_rich["reasoning"]
    assert report["teams"][1]["team_name"] == "No Fit"


def test_trade_opportunities_uses_normalized_db_without_sleeper_client(tmp_path) -> None:
    repo = build_normalized_trade_fixture(tmp_path)
    runner = FantasyToolRunner(
        client_factory=lambda: FailingMcpClient(),
        decision_repository=repo,
    )

    report = runner.trade_opportunities(
        league_id="league-1",
        roster_id=1,
        season=2026,
        week=1,
        positions="QB,RB,WR,TE",
        targets_per_team=3,
        offers_per_team=2,
    )

    assert report["data_source"] == "normalized_db"
    assert report["fallback_used"] is False
    assert report["sync_recommended"] is False
    assert [team["team_name"] for team in report["teams"]] == [
        "WR Rich",
        "No Fit",
    ]
    wr_rich = report["teams"][0]
    assert wr_rich["targets"][0]["name"] == "Target WR"
    assert wr_rich["offer_angles"][0]["ask_for"]["name"] == "Target WR"
    assert wr_rich["offer_angles"][0]["offer"][0]["name"] == "Bench TE"
    assert wr_rich["offer_angles"][0]["projected_lineup_gain"] == 6
    assert wr_rich["offer_angles"][0]["trade_score"] > 0
    repo.close()


def test_decision_smoke_report_returns_markdown_tables(tmp_path) -> None:
    runner = FantasyToolRunner(
        client_factory=lambda: FakeLineupClient(),
        players_cache=tmp_path / "players.json",
        default_league_id="league-1",
        default_roster_id=1,
    )

    report = runner.decision_smoke_report(
        season=2026,
        week=1,
        positions="RB,WR",
        per_position_limit=1,
        format="markdown",
    )

    assert isinstance(report, str)
    assert "## Current Lineup" in report
    assert "| IR | reserve | IR RB | MIA | RB | Inactive | IR | 0.00 | 18.00 | false | true |" in report
    assert "## Waiver By Position" in report


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


class FakeStatusRepository:
    def __init__(self, status: dict[str, object]) -> None:
        self.status = status

    def decision_data_status(self, *, season=None):
        return {"season": season, **self.status}


class FakeSyncService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def sync_decision_data(self, *, league_id=None, season=None, week=None, force=False):
        call = {
            "league_id": league_id,
            "season": season,
            "week": week,
            "force": force,
        }
        self.calls.append(call)
        return {"synced": True, **call}


class FailingMcpClient:
    def __enter__(self):
        raise AssertionError("normalized decision read should not call Sleeper")


class FakeTrendRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def query_numeric_stat_rows(
        self,
        *,
        source,
        season,
        start_week,
        end_week,
        stat_keys=None,
        player_ids=None,
        positions=None,
    ):
        self.calls.append(
            {
                "source": source,
                "season": season,
                "start_week": start_week,
                "end_week": end_week,
                "stat_keys": stat_keys,
                "player_ids": player_ids,
                "positions": positions,
            }
        )
        return [
            {
                "season": 2026,
                "week": 1,
                "player_id": "rb-1",
                "name": "Runner One",
                "team": "DEN",
                "position": "RB",
                "stat_key": "rush_att",
                "stat_value": 10,
            },
            {
                "season": 2026,
                "week": 2,
                "player_id": "rb-1",
                "name": "Runner One",
                "team": "DEN",
                "position": "RB",
                "stat_key": "rush_att",
                "stat_value": 12,
            },
            {
                "season": 2026,
                "week": 2,
                "player_id": "rb-2",
                "name": "Runner Two",
                "team": "KC",
                "position": "RB",
                "stat_key": "rush_att",
                "stat_value": 18,
            },
            {
                "season": 2026,
                "week": 2,
                "player_id": "wr-1",
                "name": "Wide One",
                "team": "LV",
                "position": "WR",
                "stat_key": "targets",
                "stat_value": 9,
            },
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


def build_normalized_decision_fixture(
    tmp_path,
    *,
    db_path: Path | None = None,
) -> SleeperNormalizedRepository:
    repo = SleeperNormalizedRepository(db_path or tmp_path / "normalized.db")
    repo.upsert_league_settings(
        {
            "league_id": "league-1",
            "name": "Normalized League",
            "season": 2026,
            "roster_positions": ["RB", "FLEX", "BN", "IR"],
            "scoring_settings": {"custom_score": 10},
        }
    )
    repo.upsert_league_users(
        "league-1",
        [
            {"user_id": "u1", "display_name": "Me", "metadata": {"team_name": "Me"}},
            {
                "user_id": "u2",
                "display_name": "Opponent",
                "metadata": {"team_name": "Opponent"},
            },
        ],
    )
    repo.upsert_rosters(
        "league-1",
        [
            {
                "roster_id": 1,
                "owner_id": "u1",
                "players": [
                    "starter-rb",
                    "bench-wr",
                    "drop-rb",
                    "ir-rb",
                ],
                "reserve": ["ir-rb"],
            },
            {"roster_id": 2, "owner_id": "u2", "players": ["rostered-rb"]},
        ],
    )
    repo.upsert_matchups(
        "league-1",
        2026,
        1,
        [
            {
                "roster_id": 1,
                "matchup_id": 10,
                "points": 4,
                "starters": ["starter-rb", "bench-wr"],
                "players": ["starter-rb", "bench-wr", "drop-rb"],
                "players_points": {"starter-rb": 2, "bench-wr": 2},
            },
            {
                "roster_id": 2,
                "matchup_id": 10,
                "points": 0,
                "starters": ["rostered-rb"],
                "players": ["rostered-rb"],
            },
        ],
    )
    repo.upsert_players(
        {
            "starter-rb": normalized_player("Starter RB", "RB", "KC"),
            "bench-wr": normalized_player("Bench WR", "WR", "GB"),
            "drop-rb": normalized_player("Drop RB", "RB", "LV"),
            "free-wr": normalized_player("Free WR", "WR", "SF"),
            "ir-rb": {
                **normalized_player("IR RB", "RB", "MIA"),
                "status": "Inactive",
                "injury_status": "IR",
                "depth_chart_order": 2,
                "depth_chart_position": "RB",
            },
            "free-rb": {
                **normalized_player("Free RB", "RB", "DEN"),
                "market_type": "waiver",
                "rostered_percent": 55,
            },
            "rostered-rb": normalized_player("Rostered RB", "RB", "BAL"),
        }
    )
    repo.upsert_player_week_rows(
        season=2026,
        week=1,
        source="projections",
        rows=[
            normalized_projection("starter-rb", "Starter RB", "KC", "RB", 1),
            normalized_projection("bench-wr", "Bench WR", "GB", "WR", 1.4),
            normalized_projection("drop-rb", "Drop RB", "LV", "RB", 0.5),
            normalized_projection("free-wr", "Free WR", "SF", "WR", 1.5),
            normalized_projection("ir-rb", "IR RB", "MIA", "RB", 1.8),
            normalized_projection("free-rb", "Free RB", "DEN", "RB", 3),
            normalized_projection("rostered-rb", "Rostered RB", "BAL", "RB", 4),
        ],
        scoring_settings={"custom_score": 10},
    )
    repo.upsert_transactions(
        "league-1",
        [
            {
                "transaction_id": "txn-add",
                "week": 1,
                "type": "waiver",
                "status": "complete",
                "created": 100,
                "adds": {"free-rb": 99},
                "drops": {},
            }
        ],
        week=1,
    )
    return repo


def build_normalized_trade_fixture(tmp_path) -> SleeperNormalizedRepository:
    repo = SleeperNormalizedRepository(tmp_path / "normalized-trade.db")
    repo.upsert_league_settings(
        {
            "league_id": "league-1",
            "name": "Trade League",
            "season": 2026,
            "roster_positions": ["WR", "TE", "FLEX", "BN", "BN"],
            "scoring_settings": {"custom_score": 10},
        }
    )
    repo.upsert_league_users(
        "league-1",
        [
            {"user_id": "u1", "display_name": "Me", "metadata": {"team_name": "Me"}},
            {
                "user_id": "u2",
                "display_name": "WR Rich",
                "metadata": {"team_name": "WR Rich"},
            },
            {
                "user_id": "u3",
                "display_name": "No Fit",
                "metadata": {"team_name": "No Fit"},
            },
        ],
    )
    repo.upsert_rosters(
        "league-1",
        [
            {
                "roster_id": 1,
                "owner_id": "u1",
                "players": [
                    "my-wr-low",
                    "my-te-start",
                    "my-flex",
                    "bench-te",
                    "bench-rb",
                ],
            },
            {
                "roster_id": 2,
                "owner_id": "u2",
                "players": ["target-wr", "other-wr", "third-wr", "bad-te"],
            },
            {
                "roster_id": 3,
                "owner_id": "u3",
                "players": ["low-wr", "low-rb", "low-te"],
            },
        ],
    )
    repo.upsert_matchups(
        "league-1",
        2026,
        1,
        [
            {
                "roster_id": 1,
                "matchup_id": 10,
                "points": 12,
                "starters": ["my-wr-low", "my-te-start", "my-flex"],
                "players": [
                    "my-wr-low",
                    "my-te-start",
                    "my-flex",
                    "bench-te",
                    "bench-rb",
                ],
                "players_points": {"my-wr-low": 4, "my-te-start": 5, "my-flex": 3},
            }
        ],
    )
    players = {
        "my-wr-low": normalized_player("My Low WR", "WR", "KC"),
        "my-te-start": normalized_player("My Start TE", "TE", "DEN"),
        "my-flex": normalized_player("My Flex", "RB", "LV"),
        "bench-te": normalized_player("Bench TE", "TE", "SEA"),
        "bench-rb": normalized_player("Bench RB", "RB", "GB"),
        "target-wr": normalized_player("Target WR", "WR", "MIA"),
        "other-wr": normalized_player("Other WR", "WR", "BAL"),
        "third-wr": normalized_player("Third WR", "WR", "PHI"),
        "bad-te": normalized_player("Bad TE", "TE", "NYJ"),
        "low-wr": normalized_player("Low WR", "WR", "NE"),
        "low-rb": normalized_player("Low RB", "RB", "CHI"),
        "low-te": normalized_player("Low TE", "TE", "CAR"),
    }
    repo.upsert_players(players)
    projections = {
        "my-wr-low": ("My Low WR", "KC", "WR", 1.0),
        "my-te-start": ("My Start TE", "DEN", "TE", 0.9),
        "my-flex": ("My Flex", "LV", "RB", 0.8),
        "bench-te": ("Bench TE", "SEA", "TE", 0.8),
        "bench-rb": ("Bench RB", "GB", "RB", 0.6),
        "target-wr": ("Target WR", "MIA", "WR", 1.4),
        "other-wr": ("Other WR", "BAL", "WR", 1.2),
        "third-wr": ("Third WR", "PHI", "WR", 1.1),
        "bad-te": ("Bad TE", "NYJ", "TE", 0.3),
        "low-wr": ("Low WR", "NE", "WR", 0.7),
        "low-rb": ("Low RB", "CHI", "RB", 0.6),
        "low-te": ("Low TE", "CAR", "TE", 0.5),
    }
    repo.upsert_player_week_rows(
        season=2026,
        week=1,
        source="projections",
        rows=[
            normalized_projection(player_id, name, team, position, custom_score)
            for player_id, (name, team, position, custom_score) in projections.items()
        ],
        scoring_settings={"custom_score": 10},
    )
    return repo


def normalized_player(name: str, position: str, team: str) -> dict[str, object]:
    return {
        "full_name": name,
        "team": team,
        "position": position,
        "fantasy_positions": [position],
        "status": "Active",
        "injury_status": "",
    }


def normalized_projection(
    player_id: str,
    name: str,
    team: str,
    position: str,
    custom_score: float,
) -> dict[str, object]:
    return {
        "player_id": player_id,
        "player": {"full_name": name, "team": team, "position": position},
        "stats": {"custom_score": custom_score, "pts_ppr": custom_score},
    }


def mark_normalized_fixture_stale(repo: SleeperNormalizedRepository) -> None:
    old_timestamp = 1
    for table in (
        "players",
        "league_settings",
        "league_users",
        "rosters",
        "matchups",
        "transactions",
        "player_week_rows",
    ):
        repo._connection.execute(f"UPDATE {table} SET updated_at = ?", (old_timestamp,))
    repo._connection.commit()


class FakeLineupClient(FakeMcpClient):
    def get_league(self, league_id: str) -> dict[str, object]:
        self.league_ids.append(league_id)
        return {
            "roster_positions": ["RB", "FLEX", "BN", "IR"],
            "scoring_settings": {"custom_score": 10},
        }

    def get_rosters(self, league_id: str) -> list[dict[str, object]]:
        return [
            {
                "roster_id": 1,
                "owner_id": "u1",
                "players": ["starter-rb", "bench-wr", "bench-rb", "drop-rb", "ir-rb"],
                "reserve": ["ir-rb"],
            },
            {"roster_id": 2, "owner_id": "u2", "players": ["rostered-rb"]},
        ]

    def get_matchups(self, league_id: str, week: int) -> list[dict[str, object]]:
        return [
            {
                "roster_id": 1,
                "matchup_id": 10,
                "points": 4,
                "starters": ["starter-rb", "bench-wr"],
                "players": ["starter-rb", "bench-wr", "bench-rb", "drop-rb"],
                "players_points": {"starter-rb": 2, "bench-wr": 2},
            },
            {
                "roster_id": 2,
                "matchup_id": 10,
                "points": 0,
                "starters": ["rostered-rb"],
                "players": ["rostered-rb"],
            },
        ]

    def get_players(self, *, position=None, active=None, sport="nfl") -> dict[str, dict[str, object]]:
        return {
            "starter-rb": {
                "full_name": "Starter RB",
                "team": "KC",
                "position": "RB",
                "fantasy_positions": ["RB"],
                "status": "Active",
            },
            "bench-wr": {
                "full_name": "Bench WR",
                "team": "GB",
                "position": "WR",
                "fantasy_positions": ["WR"],
                "status": "Active",
            },
            "bench-rb": {
                "full_name": "Bench RB",
                "team": "DEN",
                "position": "RB",
                "fantasy_positions": ["RB"],
                "status": "Active",
            },
            "drop-rb": {
                "full_name": "Drop RB",
                "team": "LV",
                "position": "RB",
                "fantasy_positions": ["RB"],
                "status": "Active",
            },
            "ir-rb": {
                "full_name": "IR RB",
                "team": "MIA",
                "position": "RB",
                "fantasy_positions": ["RB"],
                "status": "Inactive",
                "injury_status": "IR",
                "depth_chart_order": 2,
                "depth_chart_position": "RB",
            },
            "free-rb": {
                "full_name": "Free RB",
                "team": "DEN",
                "position": "RB",
                "fantasy_positions": ["RB"],
                "status": "Active",
                "rostered_percent": 55,
                "market_type": "waiver",
            },
            "watch-wr": {
                "full_name": "Watch WR",
                "team": "SF",
                "position": "WR",
                "fantasy_positions": ["WR"],
                "status": "Active",
                "rostered_percent": 15,
            },
            "rostered-rb": {
                "full_name": "Rostered RB",
                "team": "BAL",
                "position": "RB",
                "fantasy_positions": ["RB"],
                "status": "Active",
            },
        }

    def get_trending_players(self, trend_type, *, sport="nfl", lookback_hours=24, limit=25):
        if trend_type == "drop":
            return [{"player_id": "free-rb", "count": 100}]
        return [
            {"player_id": "free-rb", "count": 2500},
            {"player_id": "watch-wr", "count": 600},
        ]

    def _rows(self, position: str | None, *, projected: bool) -> list[dict[str, object]]:
        if position == "RB":
            return [
                self._row("starter-rb", "Starter RB", "KC", "RB", 1),
                self._row("bench-rb", "Bench RB", "DEN", "RB", 2),
                self._row("drop-rb", "Drop RB", "LV", "RB", 0.5),
                self._row("ir-rb", "IR RB", "MIA", "RB", 1.8),
                self._row("free-rb", "Free RB", "DEN", "RB", 3),
                self._row("rostered-rb", "Rostered RB", "BAL", "RB", 4),
            ]
        if position == "WR":
            return [
                self._row("bench-wr", "Bench WR", "GB", "WR", 1.4),
                self._row("watch-wr", "Watch WR", "SF", "WR", 1.5),
            ]
        return []


class FakeTradeClient(FakeLineupClient):
    def get_league(self, league_id: str) -> dict[str, object]:
        self.league_ids.append(league_id)
        return {
            "roster_positions": ["WR", "TE", "FLEX", "BN", "BN"],
            "scoring_settings": {"custom_score": 10},
        }

    def get_league_users(self, league_id: str) -> list[dict[str, object]]:
        return [
            {"user_id": "u1", "display_name": "Me", "metadata": {"team_name": "Me"}},
            {"user_id": "u2", "display_name": "WR Rich", "metadata": {"team_name": "WR Rich"}},
            {"user_id": "u3", "display_name": "No Fit", "metadata": {"team_name": "No Fit"}},
        ]

    def get_rosters(self, league_id: str) -> list[dict[str, object]]:
        return [
            {
                "roster_id": 1,
                "owner_id": "u1",
                "players": ["my-wr-low", "my-te-start", "my-flex", "bench-te", "bench-rb"],
            },
            {
                "roster_id": 2,
                "owner_id": "u2",
                "players": ["target-wr", "other-wr", "third-wr", "bad-te"],
            },
            {
                "roster_id": 3,
                "owner_id": "u3",
                "players": ["low-wr", "low-rb", "low-te"],
            },
        ]

    def get_matchups(self, league_id: str, week: int) -> list[dict[str, object]]:
        return [
            {
                "roster_id": 1,
                "matchup_id": 10,
                "points": 12,
                "starters": ["my-wr-low", "my-te-start", "my-flex"],
                "players": ["my-wr-low", "my-te-start", "my-flex", "bench-te", "bench-rb"],
                "players_points": {"my-wr-low": 4, "my-te-start": 5, "my-flex": 3},
            }
        ]

    def get_players(self, *, position=None, active=None, sport="nfl") -> dict[str, dict[str, object]]:
        return {
            "my-wr-low": self._player("My Low WR", "WR", "KC"),
            "my-te-start": self._player("My Start TE", "TE", "DEN"),
            "my-flex": self._player("My Flex", "RB", "LV"),
            "bench-te": self._player("Bench TE", "TE", "SEA"),
            "bench-rb": self._player("Bench RB", "RB", "GB"),
            "target-wr": self._player("Target WR", "WR", "MIA"),
            "other-wr": self._player("Other WR", "WR", "BAL"),
            "third-wr": self._player("Third WR", "WR", "PHI"),
            "bad-te": self._player("Bad TE", "TE", "NYJ"),
            "low-wr": self._player("Low WR", "WR", "NE"),
            "low-rb": self._player("Low RB", "RB", "CHI"),
            "low-te": self._player("Low TE", "TE", "CAR"),
        }

    def _player(self, name: str, position: str, team: str) -> dict[str, object]:
        return {
            "full_name": name,
            "team": team,
            "position": position,
            "fantasy_positions": [position],
            "status": "Active",
            "injury_status": "",
        }

    def _rows(self, position: str | None, *, projected: bool) -> list[dict[str, object]]:
        points = {
            "my-wr-low": ("My Low WR", "KC", "WR", 1.0),
            "my-te-start": ("My Start TE", "DEN", "TE", 0.9),
            "my-flex": ("My Flex", "LV", "RB", 0.8),
            "bench-te": ("Bench TE", "SEA", "TE", 0.8),
            "bench-rb": ("Bench RB", "GB", "RB", 0.6),
            "target-wr": ("Target WR", "MIA", "WR", 1.4),
            "other-wr": ("Other WR", "BAL", "WR", 1.2),
            "third-wr": ("Third WR", "PHI", "WR", 1.1),
            "bad-te": ("Bad TE", "NYJ", "TE", 0.3),
            "low-wr": ("Low WR", "NE", "WR", 0.7),
            "low-rb": ("Low RB", "CHI", "RB", 0.6),
            "low-te": ("Low TE", "CAR", "TE", 0.5),
        }
        return [
            self._row(player_id, name, team, pos, custom_score)
            for player_id, (name, team, pos, custom_score) in points.items()
            if pos == position
        ]


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
