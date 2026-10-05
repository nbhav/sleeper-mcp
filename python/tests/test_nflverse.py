from __future__ import annotations

from sleeper_tooling.nflverse import compare_stat_values, load_weekly_player_stats


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
