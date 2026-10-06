from __future__ import annotations

from sleeper_tooling.db import SleeperNormalizedRepository
from sleeper_tooling.nflverse import (
    compare_stat_values,
    load_weekly_player_stats,
    sync_nflverse_stats,
)


def test_load_weekly_player_stats_maps_ids_filters_regular_season_and_weeks() -> None:
    class FakeLoader:
        def load_ff_playerids(self):
            return [{"gsis_id": "g-1", "sleeper_id": "s-1"}]

        def load_player_stats(self, seasons):
            assert seasons == [2026]
            return [
                {"player_id": "g-1", "season": 2026, "week": 4, "season_type": "REG", "targets": 5},
                {"player_id": "g-1", "season": 2026, "week": 5, "season_type": "REG", "targets": 8},
                {"player_id": "g-1", "season": 2026, "week": 4, "season_type": "POST", "targets": 9},
                {"player_id": "unmapped", "season": 2026, "week": 4, "season_type": "REG", "targets": 2},
            ]

    assert load_weekly_player_stats([2026], weeks=[4], loader=FakeLoader()) == [
        {"player_id": "g-1", "season": 2026, "week": 4, "season_type": "REG", "targets": 5, "sleeper_id": "s-1"}
    ]


def test_load_weekly_player_stats_preserves_zeroes_and_uses_first_duplicate_identity() -> None:
    class FakeLoader:
        def load_ff_playerids(self):
            return [
                {"gsis_id": "g-1", "sleeper_id": "s-first"},
                {"gsis_id": "g-1", "sleeper_id": "s-second"},
            ]

        def load_player_stats(self, seasons):
            return [{
                "player_id": "g-1",
                "season": 2026,
                "week": 1,
                "season_type": "REG",
                "targets": 0,
                "receptions": None,
            }]

    assert load_weekly_player_stats([2026], loader=FakeLoader()) == [{
        "player_id": "g-1",
        "season": 2026,
        "week": 1,
        "season_type": "REG",
        "targets": 0,
        "receptions": None,
        "sleeper_id": "s-first",
    }]


def test_compare_stat_values_reports_only_joined_stat_mismatches() -> None:
    report = compare_stat_values(
        [
            {"player_id": "s-1", "season": 2026, "week": 4, "stat_key": "rec", "stat_value": 4},
            {"player_id": "s-1", "season": 2026, "week": 4, "stat_key": "rec_tgt", "stat_value": 4},
        ],
        [
            {"sleeper_id": "s-1", "season": 2026, "week": 4, "receptions": 4, "targets": 5},
            {"sleeper_id": "unmatched", "season": 2026, "week": 4, "receptions": 99, "targets": 99},
        ],
    )

    assert report.comparisons == 2
    assert report.match_rate == 0.5
    assert report.mismatches[0].stat_key == "rec_tgt"
    assert report.to_dict()["mismatches"][0]["nflverse_value"] == 5.0
    assert report.to_dict()["mismatches"][0]["delta"] == 1.0
    assert report.to_dict()["mismatches"][0]["direction"] == "higher"
    assert report.to_dict()["missing_nflverse"] == 0


def test_compare_stat_values_reports_coverage_missing_rows_and_direction_summary() -> None:
    report = compare_stat_values(
        [
            {"player_id": "s-1", "season": 2026, "week": 1, "stat_key": "rec", "stat_value": 2},
            {"player_id": "s-2", "season": 2026, "week": 1, "stat_key": "rec", "stat_value": 3},
        ],
        [{
            "sleeper_id": "s-1",
            "season": 2026,
            "week": 1,
            "receptions": 1,
        }],
        unresolved_identity=2,
        provider_metadata={"run_id": "parity-1"},
    )

    payload = report.to_dict()
    assert payload["sleeper_rows"] == 2
    assert payload["nflverse_rows"] == 1
    assert payload["comparisons"] == 1
    assert payload["coverage"] == 0.5
    assert payload["missing_nflverse"] == 1
    assert payload["unresolved_identity"] == 2
    assert payload["mismatch_summary"] == {
        "by_stat": {"rec": 1},
        "by_direction": {"lower": 1},
        "total_delta": -1.0,
    }
    assert payload["provider_metadata"] == {"run_id": "parity-1"}


def test_sync_nflverse_stats_keeps_source_separate_and_records_metadata(tmp_path) -> None:
    class FakeLoader:
        def load_ff_playerids(self):
            return [{"gsis_id": "g-1", "sleeper_id": "s-1"}]

        def load_player_stats(self, seasons):
            return [{
                "player_id": "g-1", "season": 2026, "week": 4,
                "season_type": "REG", "targets": 5, "receptions": 3,
                "recent_team": "DEN", "position": "WR",
            }]

    repository = SleeperNormalizedRepository(tmp_path / "sleeper.db")
    repository.upsert_player_week_rows(
        season=2026,
        week=4,
        source="stats",
        rows=[{"player_id": "s-1", "player": {"full_name": "One"}, "stats": {"targets": 4}}],
    )
    result = sync_nflverse_stats(
        repository,
        [2026],
        weeks=[4],
        loader=FakeLoader(),
        fetched_at=1234,
    )

    assert result["mapped_rows"] == 1
    assert repository.list_player_week_stat_values(
        season=2026, week=4, source="stats", player_id="s-1"
    )[0]["stat_value"] == 4
    nflverse_values = repository.list_player_week_stat_values(
        season=2026, week=4, source="nflverse_stats", player_id="s-1"
    )
    assert {row["stat_key"]: row["stat_value"] for row in nflverse_values} == {
        "rec": 3,
        "rec_tgt": 5,
    }
    metadata = repository.list_provider_sync_metadata(
        provider="nflverse", dataset="player_stats", season=2026
    )
    assert metadata[0]["fetched_at"] == 1234
    assert metadata[0]["metadata"]["source"] == "nflverse_stats"
    assert metadata[0]["metadata"]["provider"] == "nflverse"
    assert metadata[0]["metadata"]["dataset"] == "player_stats"
    assert metadata[0]["metadata"]["season_type"] == "REG"
    assert metadata[0]["metadata"]["coverage"] == {"season": 2026, "weeks": [4]}
    assert metadata[0]["metadata"]["row_count"] == 1
    assert metadata[0]["metadata"]["unresolved_identity_count"] == 0
    assert metadata[0]["metadata"]["skipped_row_count"] == 0
    repository.close()


def test_sync_nflverse_resync_replaces_only_nflverse_source_rows(tmp_path) -> None:
    class FakeLoader:
        def __init__(self):
            self.targets = 5

        def load_ff_playerids(self):
            return [{"gsis_id": "g-1", "sleeper_id": "s-1"}]

        def load_player_stats(self, seasons):
            return [{
                "player_id": "g-1", "season": 2026, "week": 4,
                "season_type": "REG", "targets": self.targets,
            }]

    repository = SleeperNormalizedRepository(tmp_path / "sleeper.db")
    repository.upsert_player_week_rows(
        season=2026,
        week=4,
        source="stats",
        rows=[{"player_id": "s-1", "stats": {"rec_tgt": 4}}],
    )
    loader = FakeLoader()
    sync_nflverse_stats(repository, [2026], weeks=[4], loader=loader, fetched_at=1000)
    loader.targets = 8
    sync_nflverse_stats(repository, [2026], weeks=[4], loader=loader, fetched_at=2000)

    assert repository.list_player_week_stat_values(
        season=2026, week=4, source="stats", player_id="s-1"
    )[0]["stat_value"] == 4
    assert repository.list_player_week_stat_values(
        season=2026, week=4, source="nflverse_stats", player_id="s-1"
    )[0]["stat_value"] == 8
    metadata = repository.list_provider_sync_metadata(
        provider="nflverse", dataset="player_stats", season=2026
    )
    assert len(metadata) == 1
    assert metadata[0]["fetched_at"] == 2000
    repository.close()


def test_sync_nflverse_records_zero_row_requested_week_metadata(tmp_path) -> None:
    class FakeLoader:
        def load_ff_playerids(self):
            return [{"gsis_id": "g-1", "sleeper_id": "s-1"}]

        def load_player_stats(self, seasons):
            return []

    repository = SleeperNormalizedRepository(tmp_path / "sleeper.db")

    result = sync_nflverse_stats(
        repository,
        [2026],
        weeks=[5],
        loader=FakeLoader(),
        fetched_at=3000,
    )

    assert result["mapped_rows"] == 0
    assert result["row_counts"] == {"2026:5": 0}
    metadata = repository.list_provider_sync_metadata(
        provider="nflverse", dataset="player_stats", season=2026
    )
    assert metadata[0]["week"] == 5
    assert metadata[0]["fetched_at"] == 3000
    assert metadata[0]["metadata"]["row_count"] == 0
    assert metadata[0]["metadata"]["coverage"] == {"season": 2026, "weeks": [5]}
    repository.close()
