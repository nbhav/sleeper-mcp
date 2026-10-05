from __future__ import annotations

import pytest

from sleeper_tooling.trend_queries import (
    GRAPH_ROW_FIELDS,
    decision_data_status,
    multi_stat_usage_trends,
    player_stat_trends,
    position_stat_leaders,
    projection_actual_deltas,
    week_over_week_movers,
)
import sleeper_tooling.trend_queries as trend_queries


def test_player_stat_trends_returns_graph_friendly_weekly_rows() -> None:
    rows = player_stat_trends(
        FakeTrendRepository(),
        season=2026,
        player_id="rb-1",
        stat_key="rush_att",
        start_week=1,
        end_week=3,
    )

    assert [row["week"] for row in rows] == [1, 2, 3]
    assert set(GRAPH_ROW_FIELDS).issubset(rows[0])
    assert rows[0] == {
        "season": 2026,
        "week": 1,
        "player_id": "rb-1",
        "name": "Runner One",
        "team": "DEN",
        "position": "RB",
        "stat_key": "rush_att",
        "stat_value": 10,
    }


def test_multi_stat_usage_trends_filters_multiple_stats_and_positions() -> None:
    rows = multi_stat_usage_trends(
        FakeTrendRepository(),
        season=2026,
        start_week=1,
        end_week=2,
        stat_keys=["rush_att", "targets"],
        positions=["RB"],
    )

    assert [(row["player_id"], row["stat_key"], row["week"]) for row in rows] == [
        ("rb-1", "rush_att", 1),
        ("rb-1", "rush_att", 2),
        ("rb-1", "targets", 1),
        ("rb-1", "targets", 2),
        ("rb-2", "rush_att", 1),
        ("rb-2", "rush_att", 2),
        ("rb-2", "targets", 1),
        ("rb-2", "targets", 2),
    ]


def test_position_stat_leaders_rank_by_stat_value() -> None:
    leaders = position_stat_leaders(
        FakeTrendRepository(),
        season=2026,
        week=2,
        position="RB",
        stat_key="rush_att",
        limit=2,
    )

    assert [row["player_id"] for row in leaders] == ["rb-2", "rb-1"]
    assert [row["position_rank"] for row in leaders] == [1, 2]
    assert set(GRAPH_ROW_FIELDS).issubset(leaders[0])


def test_projection_actual_deltas_use_stat_value_for_delta() -> None:
    rows = projection_actual_deltas(
        FakeTrendRepository(),
        season=2026,
        week=2,
        stat_keys=["targets"],
        positions=["RB"],
    )

    assert rows[0]["player_id"] == "rb-1"
    assert rows[0]["stat_key"] == "targets"
    assert rows[0]["stat_value"] == 3
    assert rows[0]["actual_value"] == 6
    assert rows[0]["projected_value"] == 3
    assert rows[0]["delta_value"] == 3
    assert rows[0]["projection_row_present"] is True
    assert rows[0]["projection_key_present"] is True


def test_projection_actual_deltas_marks_missing_projection_key_within_row() -> None:
    rows = projection_actual_deltas(
        FakeTrendRepository(),
        season=2026,
        week=1,
        stat_keys=["rush_att"],
        positions=["RB"],
    )

    assert rows[0]["player_id"] == "rb-1"
    assert rows[0]["actual_value"] == 10
    assert rows[0]["projected_value"] == 0
    assert rows[0]["delta_value"] == 10
    assert rows[0]["projection_row_present"] is True
    assert rows[0]["projection_key_present"] is False


def test_projection_actual_deltas_marks_missing_projection_row() -> None:
    rows = projection_actual_deltas(
        FakeTrendRepository(),
        season=2026,
        week=1,
        stat_keys=["rush_att"],
        player_ids=["rb-2"],
    )

    assert rows[0]["player_id"] == "rb-2"
    assert rows[0]["projected_value"] == 0
    assert rows[0]["projection_row_present"] is False
    assert rows[0]["projection_key_present"] is False


def test_projection_actual_deltas_omits_projection_only_row() -> None:
    rows = projection_actual_deltas(
        FakeTrendRepository(),
        season=2026,
        week=1,
        stat_keys=["targets"],
        positions=["WR"],
    )

    assert rows == []


def test_projection_actual_deltas_distinguishes_genuine_projected_zero() -> None:
    rows = projection_actual_deltas(
        FakeTrendRepository(),
        season=2026,
        week=3,
        stat_keys=["targets"],
        player_ids=["rb-1"],
    )

    assert rows[0]["actual_value"] == 0
    assert rows[0]["projected_value"] == 0
    assert rows[0]["delta_value"] == 0
    assert rows[0]["projection_row_present"] is True
    assert rows[0]["projection_key_present"] is True


def test_week_over_week_movers_returns_risers_and_fallers() -> None:
    report = week_over_week_movers(
        FakeTrendRepository(),
        season=2026,
        previous_week=1,
        current_week=2,
        stat_key="rush_att",
        positions=["RB"],
        limit=1,
    )

    assert report["top_risers"][0]["player_id"] == "rb-2"
    assert report["top_risers"][0]["stat_value"] == 11
    assert report["top_fallers"][0]["player_id"] == "rb-1"
    assert report["top_fallers"][0]["stat_value"] == 2


def test_decision_data_status_classifies_fresh_stale_and_missing() -> None:
    assert decision_data_status(
        FakeStatusRepository({"row_count": 3, "last_synced_at": 1000}),
        max_age_seconds=60,
        now=1020,
    )["status"] == "fresh"
    assert decision_data_status(
        FakeStatusRepository({"row_count": 3, "last_synced_at": 1000}),
        max_age_seconds=60,
        now=1200,
    )["status"] == "stale"
    assert decision_data_status(
        FakeStatusRepository({"row_count": 0, "last_synced_at": 1000}),
        max_age_seconds=60,
        now=1020,
    )["status"] == "missing"
    assert decision_data_status(
        FakeStatusRepository(None),
        season=2026,
        max_age_seconds=60,
        now=1020,
    ) == {
        "status": "missing",
        "fresh": False,
        "season": 2026,
        "row_count": 0,
        "max_age_seconds": 60,
        "evidence": ["normalized decision data status was not found"],
    }


def test_canonical_stats_prefers_nflverse_as_one_provider() -> None:
    repository = CanonicalTrendRepository()
    rows = player_stat_trends(
        repository,
        season=2026,
        player_id="wr-1",
        stat_key="targets",
        start_week=1,
        end_week=1,
        source="canonical_stats",
    )

    assert rows[0]["stat_value"] == 7
    assert repository.queried_sources == ["nflverse_stats", "nflverse_stats"]


def test_canonical_stats_falls_back_to_sleeper_when_nflverse_is_missing() -> None:
    repository = CanonicalFallbackRepository(rows=[])

    rows = player_stat_trends(
        repository,
        season=2026,
        player_id="wr-1",
        stat_key="rec_tgt",
        start_week=1,
        end_week=1,
        source="canonical_stats",
    )

    assert rows[0]["stat_value"] == 4
    assert repository.queried_sources == ["nflverse_stats", "stats"]


def test_canonical_stats_falls_back_to_sleeper_when_nflverse_is_stale(monkeypatch) -> None:
    repository = CanonicalFallbackRepository(rows=[{
        "season": 2026, "week": 1, "player_id": "wr-1",
        "name": "Wide One", "team": "DEN", "position": "WR",
        "stat_key": "rec_tgt", "stat_value": 9,
    }])
    monkeypatch.setattr(trend_queries.time, "time", lambda: 10_000)

    rows = player_stat_trends(
        repository,
        season=2026,
        player_id="wr-1",
        stat_key="rec_tgt",
        start_week=1,
        end_week=1,
        source="canonical_stats",
    )

    assert rows[0]["stat_value"] == 4
    assert repository.queried_sources == ["stats"]


def test_canonical_stats_uses_sleeper_for_kicker_and_defense() -> None:
    repository = CanonicalFallbackRepository(rows=[{
        "season": 2026, "week": 1, "player_id": "k-1",
        "name": "Kicker One", "team": "DEN", "position": "K",
        "stat_key": "fgm", "stat_value": 3,
    }])

    rows = player_stat_trends(
        repository,
        season=2026,
        player_id="k-1",
        stat_key="fgm",
        start_week=1,
        end_week=1,
        source="canonical_stats",
    )

    assert rows[0]["stat_value"] == 3
    assert repository.queried_sources == ["stats"]


def test_validate_stat_source_rejects_provider_internal_source() -> None:
    from sleeper_tooling.trend_queries import validate_stat_source

    with pytest.raises(ValueError, match="stats, projections, or canonical_stats"):
        validate_stat_source("nflverse_stats")


class CanonicalTrendRepository:
    def __init__(self):
        self.queried_sources = []

    def query_numeric_stat_rows(self, *, source, season, start_week, end_week,
                                stat_keys=None, player_ids=None, positions=None):
        self.queried_sources.append(source)
        if source == "nflverse_stats":
            return [{
                "season": season, "week": 1, "player_id": "wr-1",
                "name": "Wide One", "team": "DEN", "position": "WR",
                "stat_key": "targets", "stat_value": 7,
            }]
        return []


class CanonicalFallbackRepository:
    def __init__(self, rows):
        self.rows = rows
        self.queried_sources = []

    def list_provider_sync_metadata(self, *, provider, dataset, season):
        return [{"fetched_at": 1}]

    def query_numeric_stat_rows(self, *, source, season, start_week, end_week,
                                stat_keys=None, player_ids=None, positions=None):
        self.queried_sources.append(source)
        if source == "nflverse_stats":
            return self.rows
        return [{
            "season": season, "week": 1, "player_id": "wr-1",
            "name": "Wide One", "team": "DEN", "position": "WR",
            "stat_key": "rec_tgt", "stat_value": 4,
        }]


class FakeTrendRepository:
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
        rows = self._projection_rows() if source == "projections" else self._stat_rows()
        return [
            row
            for row in rows
            if row["season"] == season
            and start_week <= row["week"] <= end_week
            and (not player_ids or row["player_id"] in player_ids)
            and (not positions or row["position"] in positions)
        ]

    def _stat_rows(self):
        return [
            self._row(1, "rb-1", "Runner One", "RB", "rush_att", 10),
            self._row(1, "rb-1", "Runner One", "RB", "targets", 4),
            self._row(1, "rb-2", "Runner Two", "RB", "rush_att", 7),
            self._row(1, "rb-2", "Runner Two", "RB", "targets", 2),
            self._row(2, "rb-1", "Runner One", "RB", "rush_att", 12),
            self._row(2, "rb-1", "Runner One", "RB", "targets", 6),
            self._row(2, "rb-2", "Runner Two", "RB", "rush_att", 18),
            self._row(2, "rb-2", "Runner Two", "RB", "targets", 1),
            self._row(3, "rb-1", "Runner One", "RB", "rush_att", 14),
            self._row(3, "rb-1", "Runner One", "RB", "targets", 0),
        ]

    def _projection_rows(self):
        return [
            self._row(1, "rb-1", "Runner One", "RB", "targets", 3),
            self._row(1, "wr-1", "Wide One", "WR", "targets", 4),
            self._row(2, "rb-1", "Runner One", "RB", "targets", 3),
            self._row(2, "rb-2", "Runner Two", "RB", "targets", 2),
            self._row(3, "rb-1", "Runner One", "RB", "targets", 0),
        ]

    def _row(self, week, player_id, name, position, stat_key, stat_value):
        return {
            "season": 2026,
            "week": week,
            "player_id": player_id,
            "name": name,
            "team": "DEN",
            "position": position,
            "stat_key": stat_key,
            "stat_value": stat_value,
        }


class FakeStatusRepository:
    def __init__(self, status):
        self.status = status

    def decision_data_status(self, *, season=None):
        if self.status is None:
            return None
        return {"season": season, **self.status}
