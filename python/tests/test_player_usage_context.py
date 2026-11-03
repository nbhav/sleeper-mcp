"""Mocked tests for the player_usage_context v2 exact-key derived-metric contract."""

from __future__ import annotations

from typing import Any

import pytest

from sleeper_tooling.player_usage_context import (
    POSITION_OPPORTUNITY,
    POSITION_STAT_KEYS,
    STAT_KEYS,
    PlayerUsageContextService,
)
from sleeper_tooling.player_usage_context_rules import (
    DERIVED_METRIC_DEFINITIONS,
    DERIVED_METRICS_SCHEMA_VERSION,
    PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES,
    PLAYER_USAGE_CONTEXT_SCHEMA_VERSION,
    POSITION_DERIVED_METRICS,
    POSITION_USAGE_THRESHOLDS,
    REASON_CODES,
    SPIKE_THRESHOLDS,
    TREND_WINDOWS,
    compute_derived_metric,
    compute_derived_metrics,
    evaluate_player_usage_context,
    player_usage_context_contract,
    spike_reason_codes,
    trend_window,
)

FORBIDDEN_METRIC_FRAGMENTS = ("route", "target_share", "air_yard_share", "xfp", "epa", "dvoa", "war", "pace", "pressure_rate")


class FakeRepository:
    """Normalized-store stand-in: {source: {week: {stat_key: value}}}."""

    def __init__(self, position: str, weeks: dict[str, dict[int, dict[str, float]]], *, team: str = "DEN") -> None:
        self.position = position
        self.weeks = weeks
        self.team = team

    def get_player(self, player_id: str) -> dict[str, Any]:
        return {"player_id": player_id, "full_name": "Test Player", "team": self.team, "position": self.position}

    def query_numeric_stat_rows(self, *, source, season, start_week, end_week, player_ids):
        rows = []
        for week, stats in sorted(self.weeks.get(source, {}).items()):
            if start_week <= week <= end_week:
                for key, value in stats.items():
                    rows.append({"week": week, "player_id": player_ids[0], "team": self.team, "position": self.position, "stat_key": key, "stat_value": value})
        return rows

    def list_player_week_availability(self, **kwargs):
        return []

    def get_team_week_schedule(self, **kwargs):
        return None

    def list_player_role_snapshots(self, **kwargs):
        return []


def build(position: str, actual: dict[int, dict[str, float]], projections: dict[int, dict[str, float]] | None = None, *, week: int | None = None) -> dict[str, Any]:
    repo = FakeRepository(position, {"stats": actual, "projections": projections or {}})
    last_week = week or max([*actual, *(projections or {})])
    return PlayerUsageContextService(repo).build(player_id="p1", season=2026, week=last_week)


def metric(result: dict[str, Any], name: str, window: str = "season_to_date") -> dict[str, Any]:
    return result["derived_metrics"]["windows"][window]["metrics"][name]


def stats_row(week: int, stats: dict[str, float], *, source: str = "stats", active: bool = True) -> dict[str, Any]:
    return {"week": week, "source": source, "active": active, "bye": False, "raw_stats": stats}


# --- contract version and windows -------------------------------------------------


def test_contract_version_is_v3_in_output_and_contract() -> None:
    assert PLAYER_USAGE_CONTEXT_SCHEMA_VERSION == "player_usage_context.v3"
    result = build("WR", {1: {"rec_tgt": 5, "rec": 3}})
    assert result["schema_version"] == "player_usage_context.v3"
    assert DERIVED_METRICS_SCHEMA_VERSION == "player_usage_derived_metrics.v2"
    assert result["derived_metrics"]["schema_version"] == DERIVED_METRICS_SCHEMA_VERSION
    assert result["derived_metrics"]["contract_version"] == "player_usage_context.v3"
    contract = player_usage_context_contract()
    assert contract["schema_version"] == "player_usage_context.v3"
    assert contract["derived_metrics"]["schema_version"] == DERIVED_METRICS_SCHEMA_VERSION
    assert contract["derived_metrics"]["implementation"] == "python_only"
    assert set(contract["derived_metrics"]["position_metrics"]) == {"QB", "RB", "WR", "TE", "K", "DEF"}


def test_last_1_week_window_selects_latest_eligible_week() -> None:
    assert TREND_WINDOWS["last_1_week"] == {"weeks": 1, "minimum_samples": 1, "eligible_weeks": "active_non_bye"}
    rows = [stats_row(1, {}), stats_row(2, {}), stats_row(3, {}, active=False)]
    assert [row["week"] for row in trend_window(rows, "last_1_week")] == [2]

    result = build("WR", {1: {"rec_tgt": 4}, 2: {"rec_tgt": 9}})
    assert [row["week"] for row in result["windows"]["last_1_week"]["actual"]] == [2]
    assert result["windows"]["last_1_week"]["sample_size"] == 1
    window = result["derived_metrics"]["windows"]["last_1_week"]
    assert window["weeks"] == [2]
    assert window["sufficient_sample"] is True
    assert window["metrics"]["targets"]["value"] == 9
    assert window["metrics"]["targets"]["window"] == "last_1_week"
    assert result["derived_metrics"]["windows"]["season_to_date"]["metrics"]["targets"]["value"] == 13
    assert "post_change_window" not in result["derived_metrics"]["windows"]


def test_window_sufficiency_uses_minimum_samples() -> None:
    result = build("WR", {1: {"rec_tgt": 4}, 2: {"rec_tgt": 5}})
    windows = result["derived_metrics"]["windows"]
    assert windows["last_2_weeks"]["sufficient_sample"] is True
    assert windows["last_3_weeks"]["sample_size"] == 2
    assert windows["last_3_weeks"]["sufficient_sample"] is False


# --- exact-key extraction per position -------------------------------------------


@pytest.mark.parametrize(
    ("position", "stats", "expected"),
    [
        ("QB", {"pass_att": 30, "pass_cmp": 20, "pass_yd": 250, "pass_td": 2, "pass_int": 1, "pass_sack": 3, "pass_air_yd": 210, "rush_att": 4, "rush_yd": 22, "rush_td": 1, "fum": 1, "fum_lost": 1, "off_snp": 60},
         {"dropbacks": 33, "pass_attempts": 30, "pass_completions": 20, "pass_yards": 250, "pass_tds": 2, "pass_interceptions": 1, "sacks_taken": 3, "pass_air_yards": 210, "rush_attempts": 4, "rush_yards": 22, "rush_tds": 1, "fumbles": 1, "fumbles_lost": 1, "offensive_snaps": 60}),
        ("RB", {"rush_att": 15, "rush_yd": 70, "rush_td": 1, "rush_rz_att": 3, "rush_yac": 40, "rush_btkl": 2, "rush_lng": 18, "rec": 3, "rec_tgt": 5, "rec_yd": 25, "rec_td": 0, "rec_air_yd": 4, "rec_rz_tgt": 1, "rec_drop": 1, "fum": 0, "fum_lost": 0, "off_snp": 40, "st_snp": 5},
         {"rush_attempts": 15, "rush_yards": 70, "rush_tds": 1, "red_zone_rush_attempts": 3, "rush_yards_after_contact": 40, "rush_broken_tackles": 2, "longest_rush": 18, "receptions": 3, "targets": 5, "rec_yards": 25, "rec_air_yards": 4, "red_zone_targets": 1, "drops": 1, "touches": 18, "weighted_touches": 20, "offensive_snaps": 40, "special_teams_snaps": 5}),
        ("WR", {"rec_tgt": 10, "rec": 7, "rec_yd": 90, "rec_td": 1, "rec_air_yd": 120, "rec_rz_tgt": 2, "rec_drop": 1, "rush_att": 1, "rush_yd": 6, "off_snp": 55, "st_snp": 2},
         {"targets": 10, "receptions": 7, "rec_yards": 90, "rec_tds": 1, "rec_air_yards": 120, "red_zone_targets": 2, "drops": 1, "rush_attempts": 1, "rush_yards": 6, "offensive_snaps": 55, "special_teams_snaps": 2}),
        ("TE", {"rec_tgt": 6, "rec": 5, "rec_yd": 48, "rec_air_yd": 30, "rec_rz_tgt": 1, "off_snp": 50},
         {"targets": 6, "receptions": 5, "rec_yards": 48, "rec_air_yards": 30, "red_zone_targets": 1, "offensive_snaps": 50}),
        ("K", {"fga": 3, "fgm": 2, "fgmiss": 1, "xpa": 4, "xpm": 4, "fgm_lng": 52, "fgm_30_39": 1, "fgm_50_59": 1, "fgm_50p": 1, "fgmiss_40_49": 1, "st_snp": 9},
         {"fg_attempts": 3, "fg_made": 2, "fg_missed": 1, "xp_attempts": 4, "xp_made": 4, "total_kick_attempts": 7, "total_kick_makes": 6, "longest_fg": 52, "fgm_30_39": 1, "fgm_50_59": 1, "fgm_50p": 1, "fgm_60p": 0, "fgmiss_40_49": 1, "special_teams_snaps": 9}),
        ("DEF", {"sack": 4, "qb_hit": 7, "tkl_loss": 5, "int": 2, "fum_rec": 1, "def_td": 1, "def_st_td": 1, "pts_allow": 13, "yds_allow": 280},
         {"sacks": 4, "qb_hits": 7, "tackles_for_loss": 5, "interceptions": 2, "fumble_recoveries": 1, "takeaways": 3, "def_tds": 1, "def_st_tds": 1, "points_allowed": 13, "yards_allowed": 280}),
    ],
)
def test_exact_key_extraction_per_position(position: str, stats: dict[str, float], expected: dict[str, float]) -> None:
    result = build(position, {1: stats})
    derived = result["derived_metrics"]
    assert derived["position"] == position
    assert derived["metric_names"] == list(POSITION_DERIVED_METRICS[position])
    for name, value in expected.items():
        record = metric(result, name)
        assert record["value"] == value, name
        assert record["classification"] == "actual"
        assert record["source"] == "stats"
        assert record["sample_size"] == 1
        assert record["input_keys"] == DERIVED_METRIC_DEFINITIONS[name]["input_keys"]


def test_metric_records_carry_full_audit_fields() -> None:
    result = build("WR", {1: {"rec_tgt": 8, "rec": 6, "rec_yd": 80}})
    required = {"name", "value", "formula", "input_keys", "input_key_presence", "denominator_key", "denominator_value", "denominator_valid", "classification", "window", "sample_size", "reason", "weeks", "kind", "label"}
    for window in result["derived_metrics"]["windows"].values():
        for record in window["metrics"].values():
            assert required <= set(record)


def test_derived_metrics_never_include_share_or_advanced_model_names() -> None:
    for position, names in POSITION_DERIVED_METRICS.items():
        for name in names:
            assert name in DERIVED_METRIC_DEFINITIONS, (position, name)
            lowered = name.lower()
            assert not any(fragment in lowered for fragment in FORBIDDEN_METRIC_FRAGMENTS), name
            if "share" in lowered:
                assert name == "actual_snap_share_proxy"


# --- formulas and denominators ---------------------------------------------------


def test_ratio_formulas_use_ratio_of_sums() -> None:
    result = build("WR", {1: {"rec_tgt": 4, "rec": 4, "rec_yd": 40}, 2: {"rec_tgt": 6, "rec": 2, "rec_yd": 20}})
    catch_rate = metric(result, "catch_rate")
    assert catch_rate["formula"] == "sum(rec) / sum(rec_tgt)"
    assert catch_rate["value"] == pytest.approx(0.6)
    assert catch_rate["denominator_key"] == "rec_tgt"
    assert catch_rate["denominator_value"] == 10
    assert catch_rate["denominator_valid"] is True
    assert catch_rate["reason"] is None
    assert metric(result, "yards_per_target")["value"] == 6.0


def test_qb_formulas() -> None:
    result = build("QB", {1: {"pass_att": 30, "pass_cmp": 21, "pass_yd": 264, "pass_sack": 3}})
    assert metric(result, "completion_rate")["value"] == 0.7
    ypd = metric(result, "yards_per_dropback")
    assert ypd["value"] == 8.0
    assert ypd["denominator_key"] == "dropbacks"
    assert ypd["denominator_input_keys"] == ["pass_att", "pass_sack"]
    assert ypd["denominator_value"] == 33


def test_rb_touches_and_weighted_touches_are_distinct() -> None:
    result = build("RB", {1: {"rush_att": 12, "rec": 2, "rec_tgt": 6, "rush_yd": 50, "rec_yd": 20}})
    touches = metric(result, "touches")
    weighted = metric(result, "weighted_touches")
    assert touches["value"] == 14
    assert touches["input_keys"] == ["rush_att", "rec"]
    assert weighted["value"] == 18
    assert weighted["input_keys"] == ["rush_att", "rec_tgt"]
    assert metric(result, "yards_per_touch")["value"] == 5.0
    assert metric(result, "yards_per_touch")["denominator_key"] == "touches"
    row = result["evidence"]["actual_rows"][0]
    assert row["touches"] == 14
    assert row["weighted_touches"] == 18
    assert POSITION_OPPORTUNITY["RB"] == "weighted_touches"


def test_rb_opportunity_rules_read_weighted_touches_not_touches() -> None:
    assert "weighted_touches" in POSITION_USAGE_THRESHOLDS["RB"]
    assert "touches" not in POSITION_USAGE_THRESHOLDS["RB"]
    assert SPIKE_THRESHOLDS["RB"]["low_touch_floor"] == {"weighted_touches_max": 12}
    # Low receptions but a high weighted-touch count is not a low-touch spike.
    assert spike_reason_codes("RB", {"points": 22, "weighted_touches": 13, "touches": 5, "total_tds": 1}) == []
    assert spike_reason_codes("RB", {"points": 22, "weighted_touches": 12, "touches": 30, "total_tds": 1}) == ["low_touch_big_points"]
    assert spike_reason_codes("RB", {"points": 22, "touches": 5, "total_tds": 1}) == ["not_evaluable_missing_weighted_touches"]
    assert "not_evaluable_missing_weighted_touches" in REASON_CODES
    assert "not_evaluable_missing_touches" not in REASON_CODES


def test_end_to_end_rb_role_change_uses_weighted_touches() -> None:
    # Receptions stay flat while targets rise: weighted_touches drives the signal.
    actual = {week: {"rush_att": 8, "rec_tgt": 2, "rec": 1} for week in (1, 2, 3)}
    actual |= {week: {"rush_att": 12, "rec_tgt": 6, "rec": 1} for week in (4, 5)}
    result = build("RB", actual)
    assert "role_change_recent" in result["reason_codes"]


def test_zero_denominator_is_not_evaluable_not_zero() -> None:
    result = build("WR", {1: {"rec_tgt": 0, "rec": 0}})
    catch_rate = metric(result, "catch_rate")
    assert catch_rate["value"] is None
    assert catch_rate["reason"] == "denominator_zero"
    assert catch_rate["denominator_valid"] is False
    assert catch_rate["denominator_value"] == 0


def test_absent_denominator_is_not_evaluable_missing_key() -> None:
    result = build("WR", {1: {"rec": 2, "rec_yd": 15}})
    catch_rate = metric(result, "catch_rate")
    assert catch_rate["value"] is None
    assert catch_rate["reason"] == "not_evaluable_missing_rec_tgt"
    assert catch_rate["denominator_valid"] is False
    assert catch_rate["denominator_value"] is None
    qb = build("QB", {1: {"pass_yd": 10}})
    assert metric(qb, "yards_per_dropback")["reason"] == "not_evaluable_missing_dropbacks"
    assert metric(qb, "completion_rate")["reason"] == "not_evaluable_missing_pass_att"


def test_ratio_only_uses_rows_with_the_denominator() -> None:
    rows = [stats_row(1, {"rec": 3, "rec_tgt": 4}), stats_row(2, {"rec": 5})]
    record = compute_derived_metric("catch_rate", rows, window="season_to_date")
    assert record["value"] == 0.75
    assert record["denominator_weeks"] == [1]
    assert record["sample_size"] == 2


def test_composite_denominator_present_when_any_component_present() -> None:
    # A sack-free game omits pass_sack; pass_att alone still makes dropbacks present.
    rows = [stats_row(1, {"pass_att": 20, "pass_yd": 160})]
    record = compute_derived_metric("yards_per_dropback", rows, window="last_1_week")
    assert record["value"] == 8.0
    assert record["denominator_key_presence"] == {"pass_att": 1, "pass_sack": 0}
    touches = compute_derived_metric("yards_per_touch", [stats_row(1, {"rec": 2, "rec_yd": 30})], window="last_1_week")
    assert touches["value"] == 15.0


def test_absent_numerator_counts_as_zero_and_records_presence() -> None:
    result = build("WR", {1: {"rec_tgt": 5}})
    receptions = metric(result, "receptions")
    assert receptions["value"] == 0
    assert receptions["input_key_presence"] == {"rec": 0}
    assert metric(result, "catch_rate")["value"] == 0.0
    assert metric(result, "targets")["input_key_presence"] == {"rec_tgt": 1}


def test_max_metric_and_empty_window() -> None:
    record = compute_derived_metric("longest_rush", [stats_row(1, {"rush_lng": 12}), stats_row(2, {"rush_lng": 31}), stats_row(3, {})], window="season_to_date")
    assert record["value"] == 31
    missing = compute_derived_metric("longest_fg", [stats_row(1, {"fga": 1})], window="last_1_week")
    assert missing["value"] is None
    assert missing["reason"] == "not_evaluable_missing_fgm_lng"
    empty = compute_derived_metric("targets", [], window="last_1_week")
    assert empty["value"] is None
    assert empty["reason"] == "not_evaluable_missing_actual_stats"


def test_counts_report_per_game() -> None:
    result = build("RB", {1: {"rush_att": 10}, 2: {"rush_att": 15}})
    record = metric(result, "rush_attempts")
    assert record["value"] == 25
    assert record["per_game"] == 12.5


# --- snaps and air yards ---------------------------------------------------------


def test_snap_counts_stay_absolute_and_proxy_is_labeled() -> None:
    result = build("WR", {1: {"off_snp": 45, "tm_off_snp": 60, "st_snp": 4}})
    assert metric(result, "offensive_snaps")["value"] == 45
    assert metric(result, "offensive_snaps")["label"] == "absolute_count"
    assert metric(result, "special_teams_snaps")["value"] == 4
    proxy = metric(result, "actual_snap_share_proxy")
    assert proxy["value"] == 0.75
    assert proxy["label"] == "proxy"
    assert proxy["formula"] == "sum(off_snp) / sum(tm_off_snp)"
    assert proxy["denominator_key"] == "tm_off_snp"
    assert proxy["denominator_valid"] is True


def test_snap_share_proxy_requires_valid_team_denominator() -> None:
    missing = build("WR", {1: {"off_snp": 45}})
    assert metric(missing, "actual_snap_share_proxy")["value"] is None
    assert metric(missing, "actual_snap_share_proxy")["reason"] == "not_evaluable_missing_tm_off_snp"
    assert metric(missing, "offensive_snaps")["value"] == 45
    zero = build("WR", {1: {"off_snp": 0, "tm_off_snp": 0}})
    assert metric(zero, "actual_snap_share_proxy")["value"] is None
    assert metric(zero, "actual_snap_share_proxy")["reason"] == "denominator_zero"


def test_air_yards_are_absolute_evidence_and_share_stays_missing() -> None:
    result = build("WR", {1: {"rec_tgt": 8, "rec_air_yd": 95}})
    air = metric(result, "rec_air_yards")
    assert air["value"] == 95
    assert air["label"] == "absolute_count"
    assert air["input_keys"] == ["rec_air_yd"]
    assert "air_yards" not in result["missing_inputs"]
    assert "air_yard_share" in result["missing_inputs"]
    assert "not_evaluable_missing_air_yard_share" in result["reason_codes"]
    assert "not_evaluable_missing_air_yards" not in result["reason_codes"]
    assert "not_evaluable_missing_air_yards" not in REASON_CODES
    assert metric(build("QB", {1: {"pass_att": 30, "pass_air_yd": 240}}), "pass_air_yards")["value"] == 240


# --- sack alias and no silent alias summing -------------------------------------


def test_qb_dropbacks_use_pass_sack_only() -> None:
    result = build("QB", {1: {"pass_att": 30, "pass_sack": 2, "sack": 9}})
    row = result["evidence"]["actual_rows"][0]
    assert row["dropbacks"] == 32
    assert row["sacks_taken"] == 2
    assert "sacks" not in row
    assert metric(result, "dropbacks")["value"] == 32
    assert POSITION_STAT_KEYS["QB"] == {"sacks_taken": "pass_sack"}


def test_def_sacks_use_sack_only() -> None:
    result = build("DEF", {1: {"sack": 3, "pass_sack": 8}})
    row = result["evidence"]["actual_rows"][0]
    assert row["sacks"] == 3
    assert "sacks_taken" not in row
    assert row.get("dropbacks") is None
    assert metric(result, "sacks")["value"] == 3
    assert metric(result, "sacks")["input_keys"] == ["sack"]


def test_def_without_sack_key_has_no_pass_sack_fallback() -> None:
    result = build("DEF", {1: {"pass_sack": 4, "int": 1}})
    assert "sacks" not in result["evidence"]["actual_rows"][0]


def test_takeaways_sum_only_distinct_canonical_stats() -> None:
    result = build("DEF", {1: {"int": 2, "fum_rec": 1, "def_int": 5, "interceptions": 5, "fumble_recovery": 5}})
    row = result["evidence"]["actual_rows"][0]
    assert row["takeaways"] == 3
    assert metric(result, "takeaways")["value"] == 3
    assert metric(result, "takeaways")["formula"] == "int + fum_rec"


def test_legacy_alias_keys_are_ignored() -> None:
    assert all(isinstance(key, str) for key in STAT_KEYS.values())
    result = build("WR", {1: {"targets": 9, "target": 9, "receptions": 6, "rec_tgt": 4}})
    row = result["evidence"]["actual_rows"][0]
    assert row["targets"] == 4
    assert "receptions" not in row
    rb = build("RB", {1: {"carries": 20, "rush_attempts": 20}})
    assert "rush_attempts" not in rb["evidence"]["actual_rows"][0]
    assert rb["evidence"]["actual_rows"][0]["weighted_touches"] is None
    k = build("K", {1: {"fg_att": 4, "extra_point_attempts": 3, "fgm_50_plus": 2}})
    k_row = k["evidence"]["actual_rows"][0]
    assert k_row["total_kick_attempts"] is None
    assert "made_50_plus" not in k_row
    defense = build("DEF", {1: {"td": 2, "def_tds": 2, "points_allowed": 10}})
    def_row = defense["evidence"]["actual_rows"][0]
    assert "def_tds" not in def_row
    assert "points_allowed" not in def_row


def test_kicker_long_buckets_are_not_double_counted() -> None:
    result = build("K", {1: {"fgm_50_59": 1, "fgm_60p": 1, "fgm_50p": 2, "fga": 2, "fgm": 2}})
    assert metric(result, "fgm_50p")["value"] == 2
    assert metric(result, "fgm_50_59")["value"] == 1
    assert metric(result, "fgm_60p")["value"] == 1
    assert result["evidence"]["actual_rows"][0]["made_50_plus"] == 2


# --- actual versus projection ---------------------------------------------------


def test_derived_metrics_never_use_projection_rows() -> None:
    result = build("WR", {1: {"rec_tgt": 5, "rec": 3}}, {1: {"rec_tgt": 12, "rec": 9}, 2: {"rec_tgt": 12}}, week=2)
    derived = result["derived_metrics"]
    assert derived["projection_rows_used"] is False
    assert derived["classification"] == "actual"
    assert metric(result, "targets")["value"] == 5
    assert metric(result, "targets")["weeks"] == [1]


def test_projection_only_player_has_no_evaluable_derived_metrics() -> None:
    result = build("RB", {}, {1: {"rush_att": 15, "rec_tgt": 4}}, week=1)
    record = metric(result, "touches")
    assert record["value"] is None
    assert record["reason"] == "not_evaluable_missing_actual_stats"
    assert result["derived_metrics"]["windows"]["season_to_date"]["sample_size"] == 0


def test_compute_derived_metrics_drops_projection_source_rows() -> None:
    rows = [stats_row(1, {"rec_tgt": 3}), stats_row(2, {"rec_tgt": 10}, source="projections")]
    derived = compute_derived_metrics("WR", rows)
    assert derived["windows"]["season_to_date"]["metrics"]["targets"]["value"] == 3
    assert derived["windows"]["last_1_week"]["weeks"] == [1]


def test_derived_metrics_skip_inactive_and_bye_weeks() -> None:
    rows = [stats_row(1, {"rec_tgt": 3}), {**stats_row(2, {"rec_tgt": 10}), "bye": True}, stats_row(3, {"rec_tgt": 20}, active=False)]
    derived = compute_derived_metrics("WR", rows)
    assert derived["windows"]["season_to_date"]["metrics"]["targets"]["value"] == 3


def test_derived_metrics_are_deterministic() -> None:
    actual = {1: {"rec_tgt": 7, "rec": 5, "rec_yd": 61, "off_snp": 50, "tm_off_snp": 66}, 2: {"rec_tgt": 3, "rec": 1, "rec_yd": 9}}
    assert build("WR", actual)["derived_metrics"] == build("WR", actual)["derived_metrics"]
    assert metric(build("WR", actual), "actual_snap_share_proxy")["value"] == round(50 / 66, 4)


# --- golden fixtures --------------------------------------------------------------


def test_golden_fixtures_use_weighted_touches_inputs() -> None:
    for fixture in PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES:
        for group in ("recent_weeks", "season_weeks"):
            for row in fixture["inputs"].get(group, []):
                assert "touches" not in row, fixture["id"]
    rb = [fixture for fixture in PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES if fixture["position"] == "RB"]
    assert rb and all("weighted_touches" in fixture["inputs"]["recent_weeks"][0] for fixture in rb)
    for fixture in rb:
        result = evaluate_player_usage_context(fixture["position"], fixture["inputs"])
        assert result["reason_codes"] == fixture["expected"]["reason_codes"]
        assert result["capped_modifiers"] == fixture["expected"]["capped_modifiers"]


# --- v3: league repository fake ---------------------------------------------------


class LeagueRepository:
    """Multi-player normalized-store fake with raw rows, scoring values, and availability.

    stats: {(source, player_id): {week: {stat_key: value}}}
    points: {(source, player_id, week): fantasy_points or None}
    scoring: {(player_id, week): {stat_key: points}}
    availability: {(week, team): [availability rows]}
    """

    def __init__(self, players, stats, *, points=None, scoring=None, availability=None, scoring_method=True, raw=None):
        self.players = players
        self.stats = stats
        self.raw = raw or {}
        self.points = points or {}
        self.scoring = scoring or {}
        self.availability = availability
        if not scoring_method:
            self.list_player_week_scoring_values = None  # type: ignore[assignment]

    def get_player(self, player_id):
        return self.players.get(player_id, {"player_id": player_id})

    def query_numeric_stat_rows(self, *, source, season, start_week, end_week, player_ids):
        rows = []
        for player_id in player_ids:
            player = self.players.get(player_id, {})
            for week, stats in sorted(self.stats.get((source, player_id), {}).items()):
                if start_week <= week <= end_week:
                    rows.extend({"week": week, "player_id": player_id, "team": player.get("team"), "position": player.get("position"), "stat_key": key, "stat_value": value} for key, value in stats.items())
        return rows

    def list_player_week_rows(self, *, source, season, week, player_id=None):
        key = (source, player_id, week)
        if key not in self.points:
            return []
        return [{"source": source, "week": week, "player_id": player_id, "fantasy_points": self.points[key], "sleeper_points": 99.0, "raw_json": self.raw.get(key, {})}]

    def list_player_week_scoring_values(self, *, source, season, week, player_id=None):
        return [{"stat_key": key, "points": value} for key, value in self.scoring.get((player_id, week), {}).items()]

    def list_player_week_availability(self, *, season, week, team=None, player_id=None):
        if self.availability is None:
            return []
        rows = [row for (row_week, row_team), group in self.availability.items() if row_week == week and (team is None or row_team == team) for row in group]
        return [row for row in rows if player_id is None or row["player_id"] == player_id]

    def get_team_week_schedule(self, **kwargs):
        return None

    def list_player_role_snapshots(self, **kwargs):
        return []


def league_build(repo: LeagueRepository, player_id: str, week: int) -> dict[str, Any]:
    return PlayerUsageContextService(repo).build(player_id=player_id, season=2026, week=week)


# --- v3: league-scoring TD dependency ---------------------------------------------


def test_td_share_uses_league_scoring_contributions() -> None:
    players = {"wr": {"player_id": "wr", "team": "DEN", "position": "WR"}}
    stats = {("stats", "wr"): {1: {"rec_tgt": 8, "rec": 6, "rec_yd": 80, "rec_td": 1}, 2: {"rec_tgt": 8, "rec": 5, "rec_yd": 60}}}
    points = {("stats", "wr", 1): 20.0, ("stats", "wr", 2): 11.0}
    scoring = {("wr", 1): {"rec": 6.0, "rec_yd": 8.0, "rec_td": 6.0}, ("wr", 2): {"rec": 5.0, "rec_yd": 6.0}}
    result = league_build(LeagueRepository(players, stats, points=points, scoring=scoring), "wr", 2)
    td = result["td_dependency"]
    assert result["td_dependency_method"] == "league_scoring_contributions"
    assert td["source"] == "player_week_scoring_values"
    assert td["window"] == "last_2_weeks"
    assert td["weeks"] == [1, 2]
    assert td["td_scoring_keys_used"] == ["rec_td"]
    assert td["td_points"] == 6.0
    assert td["fantasy_points"] == 31.0
    assert td["value"] == round(6 / 31, 4)
    assert result["scores"]["td_dependency_score"] == round(6 / 31 * 100, 2)
    assert td["position_formula"]["formula"] == "sum(receiving_td_points) / sum(fantasy_points)"
    assert td["position_formula"]["value"] == round(6 / 31, 4)
    assert td["position_formula"]["above_threshold"] is False
    assert "not_evaluable_missing_scoring_contributions" not in result["reason_codes"]
    # League fantasy_points (not sleeper_points) is the row points value.
    assert [row["points"] for row in result["evidence"]["actual_rows"]] == [20.0, 11.0]


def test_td_share_reflects_non_standard_td_values() -> None:
    players = {"qb": {"player_id": "qb", "team": "DEN", "position": "QB"}}
    stats = {("stats", "qb"): {1: {"pass_att": 30, "pass_td": 2, "rush_td": 1, "pass_yd": 250, "pass_int": 1}}}

    def qb_result(pass_td_points: float) -> dict[str, Any]:
        contributions = {"pass_td": 2 * pass_td_points, "rush_td": 6.0, "pass_yd": 10.0, "pass_int_td": -1.0}
        total = sum(contributions.values())
        repo = LeagueRepository(players, stats, points={("stats", "qb", 1): total}, scoring={("qb", 1): contributions})
        return league_build(repo, "qb", 1)

    six_point = qb_result(6.0)["td_dependency"]
    four_point = qb_result(4.0)["td_dependency"]
    # 6-pt passing TD league: (12 + 6) / 27.
    assert six_point["td_points"] == 18.0
    assert six_point["fantasy_points"] == 27.0
    assert six_point["value"] == round(18 / 27, 4)
    assert six_point["td_scoring_keys_used"] == ["pass_td", "rush_td"]
    assert six_point["excluded_keys_present"] == ["pass_int_td"]
    assert six_point["position_formula"]["numerator_keys_used"] == ["pass_td"]
    assert six_point["position_formula"]["value"] == round(12 / 27, 4)
    # Same stat line in a 4-pt league gives a different share: no fixed heuristic.
    assert four_point["value"] == round(14 / 23, 4)
    assert four_point["value"] != six_point["value"]


def test_td_family_classifier_covers_bonus_and_defensive_keys() -> None:
    from sleeper_tooling.player_usage_context_rules import td_scoring_key_family

    assert td_scoring_key_family("pass_td") == "passing_td"
    assert td_scoring_key_family("rec_td_40p") == "receiving_td"
    assert td_scoring_key_family("bonus_rush_td_qb") == "rushing_td"
    assert td_scoring_key_family("def_st_td") == "defense_special_teams_td"
    assert td_scoring_key_family("bonus_def_int_td_50p") == "defense_special_teams_td"
    assert td_scoring_key_family("st_td") == "defense_special_teams_td"
    assert td_scoring_key_family("fum_rec_td") == "other_td"
    assert td_scoring_key_family("pass_int_td") is None
    assert td_scoring_key_family("pass_yd") is None
    assert td_scoring_key_family("td") is None


def test_def_td_dependency_counts_def_tds_and_turnovers() -> None:
    players = {"den": {"player_id": "den", "team": "DEN", "position": "DEF"}}
    stats = {("stats", "den"): {1: {"sack": 3, "int": 2, "def_td": 1, "pts_allow": 17}}}
    repo = LeagueRepository(players, stats, points={("stats", "den", 1): 13.0}, scoring={("den", 1): {"sack": 3.0, "int": 4.0, "def_td": 6.0}})
    td = league_build(repo, "den", 1)["td_dependency"]
    assert td["td_points"] == 6.0
    assert td["value"] == round(6 / 13, 4)
    assert td["position_formula"]["numerator_keys_used"] == ["def_td", "int"]
    assert td["position_formula"]["value"] == round(10 / 13, 4)
    assert td["position_formula"]["above_threshold"] is True


def test_missing_scoring_contributions_emit_explicit_code_without_heuristic() -> None:
    players = {"wr": {"player_id": "wr", "team": "DEN", "position": "WR"}}
    stats = {("stats", "wr"): {1: {"rec_tgt": 3, "rec_td": 2}}}
    # No scoring-values primitive at all.
    no_method = league_build(LeagueRepository(players, stats, points={("stats", "wr", 1): 24.0}, scoring_method=False), "wr", 1)
    # League scoring did not run for the row (fantasy_points is null).
    null_points = league_build(LeagueRepository(players, stats, points={("stats", "wr", 1): None}, scoring={("wr", 1): {"rec_td": 12.0}}), "wr", 1)
    # No raw row at all.
    no_raw = build("WR", {1: {"rec_tgt": 3, "rec_td": 2}})
    for result in (no_method, null_points, no_raw):
        assert "not_evaluable_missing_scoring_contributions" in result["reason_codes"]
        assert result["td_dependency"]["value"] is None
        assert result["td_dependency"]["reason"] == "not_evaluable_missing_scoring_contributions"
        assert result["td_dependency"]["missing_contribution_weeks"] == [1]
        assert result["scores"]["td_dependency_score"] is None
        assert result["td_dependency_method"] == "league_scoring_contributions"
    reason = next(row for row in no_method["reasons"] if row["code"] == "not_evaluable_missing_scoring_contributions")
    assert reason["computability_tier"] == "not_evaluable_missing_source"


def test_empty_contributions_with_league_points_is_a_real_zero() -> None:
    players = {"rb": {"player_id": "rb", "team": "DEN", "position": "RB"}}
    stats = {("stats", "rb"): {1: {"rush_att": 3}}}
    result = league_build(LeagueRepository(players, stats, points={("stats", "rb", 1): 0.0}, scoring={}), "rb", 1)
    assert result["td_dependency"]["reason"] == "denominator_non_positive"
    assert "not_evaluable_missing_scoring_contributions" not in result["reason_codes"]
    stats = {("stats", "rb"): {1: {"rush_att": 10, "rush_yd": 50}}}
    result = league_build(LeagueRepository(players, stats, points={("stats", "rb", 1): 5.0}, scoring={("rb", 1): {"rush_yd": 5.0}}), "rb", 1)
    assert result["td_dependency"]["value"] == 0.0
    assert result["scores"]["td_dependency_score"] == 0.0


def test_td_dependency_formulas_are_evaluated_not_strings_with_max() -> None:
    from sleeper_tooling.player_usage_context_rules import TD_DEPENDENCY_FORMULAS

    contract = player_usage_context_contract()
    assert contract["td_dependency"]["method"] == "league_scoring_contributions"
    assert contract["td_dependency"]["excluded_keys"] == ["pass_int_td"]
    for position, formula in TD_DEPENDENCY_FORMULAS.items():
        assert formula["numerator_families"], position
        assert "max(" not in formula["formula"], position


# --- v3: kicker long-kick share from real keys ------------------------------------


def test_long_kick_point_share_uses_league_bucket_points() -> None:
    players = {"k": {"player_id": "k", "team": "DEN", "position": "K"}}
    stats = {("stats", "k"): {1: {"fga": 1, "fgm": 1, "fgm_50_59": 1, "fgm_50p": 1, "fgm_lng": 52, "xpa": 2, "xpm": 2}}}
    repo = LeagueRepository(players, stats, points={("stats", "k", 1): 7.0}, scoring={("k", 1): {"fgm_50_59": 5.0, "xpm": 2.0}})
    result = league_build(repo, "k", 1)
    row = result["evidence"]["actual_rows"][0]
    assert row["long_kick_point_share"] == round(5 / 7, 4)
    assert row["made_50_plus"] == 1
    assert "long_kick_spike" in result["reason_codes"]


def test_long_kick_point_share_not_separable_when_league_scores_fg_yards() -> None:
    players = {"k": {"player_id": "k", "team": "DEN", "position": "K"}}
    stats = {("stats", "k"): {1: {"fga": 1, "fgm": 1, "fgm_50_59": 1, "fgm_50p": 1, "fgm_yds": 52, "xpa": 2, "xpm": 2}}}
    repo = LeagueRepository(players, stats, points={("stats", "k", 1): 7.2}, scoring={("k", 1): {"fgm_yds": 5.2, "xpm": 2.0}})
    result = league_build(repo, "k", 1)
    row = result["evidence"]["actual_rows"][0]
    assert row["long_kick_point_share"] is None
    assert row["long_kick_point_share_reason"] == "not_evaluable_long_field_goal_points_not_separable"
    assert "long_kick_spike" not in result["reason_codes"]
    assert result["td_dependency"]["position_formula"]["reason"] == "not_evaluable_long_field_goal_points_not_separable"
    # Legacy fgm_50_plus is never read.
    assert spike_reason_codes("K", {"fgm_50_plus": 3, "long_kick_point_share": None}) == []


# --- v3: projection presence flags ------------------------------------------------


def test_projection_row_and_key_presence_flags() -> None:
    players = {"wr": {"player_id": "wr", "team": "DEN", "position": "WR"}}
    stats = {
        ("stats", "wr"): {1: {"rec_tgt": 6}, 2: {"rec_tgt": 7}, 3: {"rec_tgt": 5}},
        # Week 1: no projection row. Week 2: projection row without rec_tgt or points.
        # Week 3: genuine projected zero for rec_tgt and points.
        ("projections", "wr"): {2: {"rec": 3}, 3: {"rec_tgt": 0}},
    }
    points = {("stats", "wr", 1): 10.0, ("stats", "wr", 2): 12.0, ("stats", "wr", 3): 8.0, ("projections", "wr", 3): 0.0}
    result = league_build(LeagueRepository(players, stats, points=points), "wr", 3)
    by_week = {row["week"]: row for row in result["projection_comparison"]}
    assert by_week[1]["projection_row_present"] is False
    assert by_week[1]["projection_key_present"] == {"points": False, "rec_tgt": False}
    assert by_week[1]["projected_points"] is None
    assert by_week[1]["projection_delta"] is None
    assert by_week[2]["projection_row_present"] is True
    assert by_week[2]["projection_key_present"] == {"points": False, "rec_tgt": False}
    assert by_week[2]["projected_values"] == {"rec_tgt": None}
    assert by_week[2]["projection_delta"] is None
    assert by_week[3]["projection_row_present"] is True
    assert by_week[3]["projection_key_present"] == {"points": True, "rec_tgt": True}
    assert by_week[3]["projected_values"] == {"rec_tgt": 0}
    assert by_week[3]["projected_points"] == 0.0
    assert by_week[3]["projection_delta"] == -8.0


def test_projection_comparison_and_td_dependency_label_source_and_classification() -> None:
    players = {"wr": {"player_id": "wr", "team": "DEN", "position": "WR"}}
    stats = {("stats", "wr"): {1: {"rec_tgt": 6}}, ("projections", "wr"): {1: {"rec_tgt": 5, "pts_ppr": 9}}}
    result = league_build(LeagueRepository(players, stats, points={("stats", "wr", 1): 10.0}, scoring={("wr", 1): {"rec_yd": 10.0}}), "wr", 1)
    entry = result["projection_comparison"][0]
    assert (entry["classification"], entry["actual_source"], entry["projection_source"]) == ("same_week_delta", "stats", "projections")
    assert result["td_dependency"]["classification"] == "actual"
    assert result["td_dependency"]["source"] == "player_week_scoring_values"


def test_raw_weekly_identity_fields_are_evidence_without_matchup_claims() -> None:
    players = {"wr": {"player_id": "wr", "team": "DEN", "position": "WR"}}
    stats = {("stats", "wr"): {1: {"rec_tgt": 6}}}
    raw = {("stats", "wr", 1): {"opponent": "KC", "game_id": "202609101", "date": "2026-09-10"}}
    result = league_build(LeagueRepository(players, stats, points={("stats", "wr", 1): 10.0}, raw=raw), "wr", 1)
    row = result["evidence"]["actual_rows"][0]
    assert row["raw"] == {"opponent": "KC", "game_id": "202609101", "date": "2026-09-10"}
    assert result["scores"]["matchup_adjustment"] == 0.0
    assert "matchup" not in result["modifiers"]
    assert "not_evaluable_missing_nfl_schedule" in result["reason_codes"]
    assert not any(code in result["reason_codes"] for code in ("matchup_opponent_label_present", "not_evaluable_missing_matchup_strength"))


def test_projection_delta_uses_same_week_only() -> None:
    players = {"wr": {"player_id": "wr", "team": "DEN", "position": "WR"}}
    # Latest actual is week 2; the only projection row is week 1.
    stats = {("stats", "wr"): {1: {"rec_tgt": 6}, 2: {"rec_tgt": 7}}, ("projections", "wr"): {1: {"rec_tgt": 5, "pts_ppr": 30}}}
    result = league_build(LeagueRepository(players, stats, points={("stats", "wr", 1): 10.0, ("stats", "wr", 2): 12.0}), "wr", 2)
    assert all("projection_delta" not in row for row in result["evidence"]["actual_rows"])
    assert result["projection_comparison"][-1]["projection_row_present"] is False


# --- v3: change-point detection / post_change_window ------------------------------


def rb_rows(values: list[float | None], *, bye_weeks: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    return [{"week": week, "active": True, "bye": week in bye_weeks, "weighted_touches": value} for week, value in enumerate(values, start=1)]


def test_change_point_none_for_flat_series() -> None:
    from sleeper_tooling.player_usage_context_rules import detect_change_point

    result = detect_change_point("RB", rb_rows([10, 11, 10, 11, 10, 11]))
    assert result["status"] == "no_change"
    assert result["post_change_weeks"] == []
    assert detect_change_point("RB", rb_rows([10, 11, 12]))["status"] == "no_change"
    assert detect_change_point("RB", rb_rows([10, 18]))["status"] == "insufficient_sample"
    assert detect_change_point("RB", [{"week": 1, "active": True}, {"week": 2, "active": True}])["status"] == "not_evaluable_missing_weighted_touches"


def test_change_point_detects_week4_role_change() -> None:
    from sleeper_tooling.player_usage_context_rules import detect_change_point

    result = detect_change_point("RB", rb_rows([10, 10, 10, 14, 14, 14, 14]))
    assert result["status"] == "change_detected"
    assert result["change_week"] == 4
    assert result["direction"] == "increased"
    assert result["post_change_weeks"] == [4, 5, 6, 7]
    assert result["pre_change_weeks"] == [1, 2, 3]
    assert result["pre_change_average"] == 10.0
    assert result["post_change_average"] == 14.0


def test_noisy_single_week_spike_is_not_a_change() -> None:
    from sleeper_tooling.player_usage_context_rules import detect_change_point

    assert detect_change_point("RB", rb_rows([10, 10, 10, 16, 10]))["status"] == "no_change"
    assert detect_change_point("RB", rb_rows([10, 10, 10, 20, 10, 10]))["status"] == "no_change"
    # A two-week shift that reverts before the latest week is not sustained.
    assert detect_change_point("RB", rb_rows([10, 10, 10, 16, 16, 10]))["status"] == "no_change"
    # Bye and inactive weeks are skipped, not read as a usage drop.
    with_bye = detect_change_point("RB", rb_rows([10, 10, 10, 16, 0, 16], bye_weeks=(5,)))
    assert with_bye["status"] == "change_detected"
    assert with_bye["post_change_weeks"] == [4, 6]


def test_week4_change_evaluated_at_week7_uses_post_change_window_end_to_end() -> None:
    weeks = {week: {"rush_att": value, "rec_tgt": 0} for week, value in enumerate([10, 10, 10, 14, 14, 14, 14], start=1)}
    result = build("RB", weeks)
    trend = result["trend_window"]
    assert trend["name"] == "post_change_window"
    assert trend["weeks"] == [4, 5, 6, 7]
    assert trend["baseline_weeks"] == [1, 2, 3]
    assert trend["change_point"]["change_week"] == 4
    assert "role_change_recent" in result["reason_codes"]
    assert result["evidence"]["recent_actual_weeks"] == [4, 5, 6, 7]
    assert result["evidence"]["baseline_weeks"] == [1, 2, 3]
    window = result["windows"]["post_change_window"]
    assert window["detected"] is True
    assert [row["week"] for row in window["actual"]] == [4, 5, 6, 7]
    derived = result["derived_metrics"]["windows"]["post_change_window"]
    assert derived["weeks"] == [4, 5, 6, 7]
    assert derived["metrics"]["rush_attempts"]["value"] == 56
    # The old fixed split (last 2 vs weeks 1-5) would not have flagged this.
    rows = rb_rows([10, 10, 10, 14, 14, 14, 14])
    from sleeper_tooling.player_usage_context_rules import opportunity_signal

    assert opportunity_signal("RB", rows, metric="weighted_touches")["status"] == "stable"


def test_no_change_keeps_fixed_trend_window() -> None:
    result = build("WR", {1: {"rec_tgt": 6}, 2: {"rec_tgt": 6}, 3: {"rec_tgt": 12}, 4: {"rec_tgt": 6}})
    assert result["trend_window"]["name"] == "last_2_weeks"
    assert result["trend_window"]["change_point"]["status"] == "no_change"
    assert result["windows"]["post_change_window"]["detected"] is False
    assert result["windows"]["post_change_window"]["actual"] == []
    assert "post_change_window" not in result["derived_metrics"]["windows"]


# --- v3: injury_replacement --------------------------------------------------------


def injury_repo(*, availability, teammate_baseline: float = 15.0, teammate_position: str = "RB") -> LeagueRepository:
    players = {
        "backup": {"player_id": "backup", "full_name": "Backup Back", "team": "DEN", "position": "RB"},
        "starter": {"player_id": "starter", "full_name": "Starter Back", "team": "DEN", "position": teammate_position},
    }
    backup = {week: {"rush_att": value, "rec_tgt": 1} for week, value in ((1, 5), (2, 6), (3, 5), (4, 16), (5, 16))}
    starter = {week: {"rush_att": teammate_baseline, "rec_tgt": 0, "rec": 0} for week in (1, 2, 3)}
    return LeagueRepository(players, {("stats", "backup"): backup, ("stats", "starter"): starter}, availability=availability)


def availability_row(player_id: str, week: int, status: str, injury_status: str | None = None) -> dict[str, Any]:
    return {"season": 2026, "week": week, "team": "DEN", "player_id": player_id, "status": status, "injury_status": injury_status, "source": "sleeper_players_current_metadata", "snapshot_at": 1760000000.0 + week}


def test_injury_replacement_with_teammate_absence() -> None:
    availability = {(week, "DEN"): [availability_row("starter", week, "Inactive", "Out"), availability_row("backup", week, "Active")] for week in (4, 5)}
    result = league_build(injury_repo(availability=availability), "backup", 5)
    assert result["reason_codes"][:2] == ["role_change_recent", "injury_replacement"]
    assert result["modifiers"]["context"] == 2.0
    assert result["role_label"] == "injury_fill_in"
    injury = result["injury_replacement"]
    assert injury["status"] == "injury_replacement"
    assert injury["evidence_source"] == "player_week_availability"
    assert injury["trend_weeks"] == [4, 5]
    teammate = injury["teammates"][0]
    assert teammate["player_id"] == "starter"
    assert [week["source"] for week in teammate["weeks"]] == ["sleeper_players_current_metadata"] * 2
    assert [week["snapshot_at"] for week in teammate["weeks"]] == [1760000004.0, 1760000005.0]
    assert teammate["weeks"][0]["baseline_opportunity_per_game"] == 15.0
    assert "not_evaluable_missing_teammate_availability" not in result["reason_codes"]


def test_injury_replacement_modifier_is_bounded_below_durable_role_change() -> None:
    fixture = next(f for f in PLAYER_USAGE_CONTEXT_GOLDEN_FIXTURES if f["id"] == "rb_injury_fill_in")
    with_injury = evaluate_player_usage_context("RB", fixture["inputs"])
    durable = evaluate_player_usage_context("RB", {key: value for key, value in fixture["inputs"].items() if key != "teammate_availability"})
    assert with_injury["reason_codes"] == ["role_change_recent", "injury_replacement"]
    assert durable["reason_codes"] == ["role_change_recent"]
    assert 0 < with_injury["capped_modifiers"]["context"] < durable["capped_modifiers"]["context"]
    assert REASON_CODES["injury_replacement"]["computability_tier"] == "partial"


def test_missing_teammate_availability_is_explicit() -> None:
    result = league_build(injury_repo(availability=None), "backup", 5)
    assert "role_change_recent" in result["reason_codes"]
    assert "not_evaluable_missing_teammate_availability" in result["reason_codes"]
    assert "injury_replacement" not in result["reason_codes"]
    assert result["modifiers"]["context"] == 4.0
    assert result["injury_replacement"]["missing_weeks"] == [4, 5]
    # Availability for only one of the trend weeks is still not evaluable.
    partial = league_build(injury_repo(availability={(4, "DEN"): [availability_row("starter", 4, "Inactive", "Out")]}), "backup", 5)
    assert "not_evaluable_missing_teammate_availability" in partial["reason_codes"]
    assert partial["injury_replacement"]["missing_weeks"] == [5]


def test_injury_replacement_requires_relevant_same_position_teammate_every_week() -> None:
    absent_both = {(week, "DEN"): [availability_row("starter", week, "Inactive", "IR")] for week in (4, 5)}
    # Teammate without pre-change usage (e.g. a buried IR player) does not qualify.
    low_usage = league_build(injury_repo(availability=absent_both, teammate_baseline=1.0), "backup", 5)
    assert "injury_replacement" not in low_usage["reason_codes"]
    assert "not_evaluable_missing_teammate_availability" not in low_usage["reason_codes"]
    assert low_usage["injury_replacement"]["status"] == "no_teammate_absence"
    # A different-position teammate does not qualify.
    other_position = league_build(injury_repo(availability=absent_both, teammate_position="WR"), "backup", 5)
    assert "injury_replacement" not in other_position["reason_codes"]
    # Absent in week 4 but back in week 5: no coincidence for the whole window.
    returned = {(4, "DEN"): [availability_row("starter", 4, "Inactive", "Out")], (5, "DEN"): [availability_row("starter", 5, "Active")]}
    back = league_build(injury_repo(availability=returned), "backup", 5)
    assert "injury_replacement" not in back["reason_codes"]
    assert back["modifiers"]["context"] == 4.0


def test_injury_replacement_needs_teammate_availability_input_in_pure_rules() -> None:
    rows = rb_rows([6, 7, 6, 17, 17])
    result = evaluate_player_usage_context("RB", {"season_weeks": rows[:3], "recent_weeks": rows[3:]})
    assert result["reason_codes"] == ["role_change_recent"]
    assert result["injury_replacement"] is None
    missing = evaluate_player_usage_context("RB", {"season_weeks": rows[:3], "recent_weeks": rows[3:], "teammate_availability": None})
    assert missing["reason_codes"] == ["role_change_recent", "not_evaluable_missing_teammate_availability"]
    assert missing["capped_modifiers"] == {"context": 4.0}


# --- v3: red-zone share and production repository wiring --------------------------


def test_red_zone_share_replaces_red_zone_use() -> None:
    for position, stats in (("RB", {"rush_att": 10, "rush_rz_att": 3, "rec_rz_tgt": 1}), ("TE", {"rec_tgt": 5, "rec_rz_tgt": 2})):
        result = build(position, {1: stats})
        assert "red_zone_share" in result["missing_inputs"]
        assert "red_zone_use" not in result["missing_inputs"]
        assert "not_evaluable_missing_red_zone_share" in result["reason_codes"]
        assert "not_evaluable_missing_red_zone_use" not in result["reason_codes"]
        assert metric(result, "red_zone_targets")["value"] == stats["rec_rz_tgt"]
    assert "not_evaluable_missing_red_zone_share" in REASON_CODES
    assert "not_evaluable_missing_red_zone_use" not in REASON_CODES


def test_service_resolves_methods_on_production_adapter_and_inner_store() -> None:
    inner = LeagueRepository({"wr": {"player_id": "wr", "team": "DEN", "position": "WR"}}, {("stats", "wr"): {1: {"rec_tgt": 5}}}, points={("stats", "wr", 1): 9.0}, scoring={("wr", 1): {"rec_yd": 9.0}})

    class Adapter:
        """Mirrors SQLiteNormalizedRepositoryAdapter: query on the adapter, list_* on .repository."""

        def __init__(self, repository):
            self.repository = repository

        def query_numeric_stat_rows(self, **kwargs):
            return self.repository.query_numeric_stat_rows(**kwargs)

    inner_query = inner.query_numeric_stat_rows
    inner.query_numeric_stat_rows = None  # type: ignore[assignment]
    adapter = Adapter(inner)
    adapter.query_numeric_stat_rows = lambda **kwargs: inner_query(**kwargs)  # type: ignore[method-assign]
    result = PlayerUsageContextService(adapter).build(player_id="wr", season=2026, week=1)
    assert result["evidence"]["season_actual_weeks"] == [1]
    assert result["td_dependency"]["value"] == 0.0
