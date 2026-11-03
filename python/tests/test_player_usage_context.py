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


def test_contract_version_is_v2_in_output_and_contract() -> None:
    assert PLAYER_USAGE_CONTEXT_SCHEMA_VERSION == "player_usage_context.v2"
    result = build("WR", {1: {"rec_tgt": 5, "rec": 3}})
    assert result["schema_version"] == "player_usage_context.v2"
    assert result["derived_metrics"]["schema_version"] == DERIVED_METRICS_SCHEMA_VERSION
    assert result["derived_metrics"]["contract_version"] == "player_usage_context.v2"
    contract = player_usage_context_contract()
    assert contract["schema_version"] == "player_usage_context.v2"
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
