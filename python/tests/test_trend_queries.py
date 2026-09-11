from __future__ import annotations

from sleeper_tooling.trend_queries import (
    GRAPH_ROW_FIELDS,
    decision_data_status,
    multi_stat_usage_trends,
    player_stat_trends,
    position_stat_leaders,
    projection_actual_deltas,
    week_over_week_movers,
)


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
        ]

    def _stat_rows(self):
        return [
            self._row(1, "rb-1", "Runner One", "RB", "rush_att", 10),
            self._row(1, "rb-1", "Runner One", "RB", "targets", 4),
            self._row(1, "rb-2", "Runner Two", "RB", "rush_att", 7),
            self._row(1, "rb-2", "Runner Two", "RB", "targets", 2),
            self._row(1, "wr-1", "Wide One", "WR", "targets", 9),
            self._row(2, "rb-1", "Runner One", "RB", "rush_att", 12),
            self._row(2, "rb-1", "Runner One", "RB", "targets", 6),
            self._row(2, "rb-2", "Runner Two", "RB", "rush_att", 18),
            self._row(2, "rb-2", "Runner Two", "RB", "targets", 1),
            self._row(3, "rb-1", "Runner One", "RB", "rush_att", 14),
        ]

    def _projection_rows(self):
        return [
            self._row(2, "rb-1", "Runner One", "RB", "targets", 3),
            self._row(2, "rb-2", "Runner Two", "RB", "targets", 2),
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
