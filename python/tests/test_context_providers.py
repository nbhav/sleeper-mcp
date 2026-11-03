from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from sleeper_tooling.context_backtest import (
    BacktestFixture,
    CalibrationRules,
    DEFAULT_FIXTURES,
    run_backtest,
)
from sleeper_tooling.context_providers import (
    NOT_EVALUABLE_MISSING_IMPLIED_TOTALS,
    NOT_EVALUABLE_MISSING_PRESSURE,
    NOT_EVALUABLE_MISSING_ROUTES,
    NOT_EVALUABLE_MISSING_WEATHER,
    PROVIDER_DATA_STALE,
    PROVIDER_DENOMINATOR_MISSING,
    PROVIDER_DUPLICATE_RECORD,
    PROVIDER_IDENTITY_UNRESOLVED,
    PROVIDER_REASON_CODES,
    ContextInput,
    ContextProviderRegistry,
    ContextProviderResult,
    ContextRequest,
    EnrichedPlayerContext,
    FreshnessStatus,
    GameIdentity,
    HomeAway,
    LookupStatus,
    MetricScope,
    MissingnessScope,
    ProviderLicense,
    ProviderMetricObservation,
    RawProviderPayload,
    freshness_status,
    lookup_observation,
    missing_input_code,
    validate_observation,
    validate_observations,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "provider_contract_sample.json"


def test_missing_provider_inputs_return_explicit_not_evaluable_codes() -> None:
    registry = ContextProviderRegistry()

    missing = registry.missing_inputs(
        [
            ContextInput.ROUTES,
            ContextInput.TARGET_SHARE,
            ContextInput.WEATHER,
        ],
        player_id="player-1",
    )

    assert [row.to_dict() for row in missing] == [
        {
            "input": "routes",
            "code": "not_evaluable_missing_routes",
            "scope": "player",
            "season": None,
            "week": None,
            "player_id": "player-1",
            "provider": None,
            "reason": "no configured context provider advertises routes",
        },
        {
            "input": "target_share",
            "code": "not_evaluable_missing_target_share",
            "scope": "player",
            "season": None,
            "week": None,
            "player_id": "player-1",
            "provider": None,
            "reason": "no configured context provider advertises target_share",
        },
        {
            "input": "weather",
            "code": "not_evaluable_missing_weather",
            "scope": "player",
            "season": None,
            "week": None,
            "player_id": "player-1",
            "provider": None,
            "reason": "no configured context provider advertises weather",
        },
    ]


def test_registry_reports_missing_inputs_not_advertised() -> None:
    provider = FakeContextProvider()
    registry = ContextProviderRegistry([provider])

    assert registry.provided_inputs() == {
        ContextInput.SNAPS,
        ContextInput.ROUTE_PARTICIPATION,
        ContextInput.TARGET_SHARE,
    }
    missing = registry.missing_inputs(["snaps", "air-yards"])

    assert [row.code for row in missing] == ["not_evaluable_missing_air_yards"]
    assert (
        missing_input_code("implied totals")
        == "not_evaluable_missing_implied_totals"
    )


def test_normalized_context_stays_separate_from_raw_provider_payload() -> None:
    result = FakeContextProvider().fetch_context(
        ContextRequest(season=2026, week=4, player_ids=["player-1"])
    )

    serialized = result.to_dict()

    assert serialized["contexts"] == [
        {
            "season": 2026,
            "week": 4,
            "player_id": "player-1",
            "team": "DEN",
            "opponent": None,
            "snaps": 42.0,
            "routes": None,
            "route_participation": 0.72,
            "target_share": 0.24,
            "carry_share": None,
            "red_zone_use": None,
            "air_yards": None,
            "pressure": None,
            "pace": None,
            "implied_totals": None,
            "weather": None,
            "source": "fake-provider",
            "fetched_at": 1000.0,
            "provenance": {},
            "provided_inputs": ["route_participation", "snaps", "target_share"],
        }
    ]
    assert serialized["raw_payloads"] == [
        {
            "provider": "fake-provider",
            "source": "usage-endpoint",
            "payload": {"provider_player_id": "abc", "snap_count": 42},
            "fetched_at": 1000.0,
            "metadata": {"schema": "provider-native"},
        }
    ]
    assert "payload" not in serialized["contexts"][0]


def test_registry_merges_disjoint_provider_fields_and_tracks_provenance() -> None:
    request = ContextRequest(season=2026, week=4, player_ids=["player-1"])
    result = ContextProviderRegistry(
        [FakeContextProvider(), WeatherProvider()]
    ).fetch_context(request, required_inputs=["snaps", "weather"])

    context = result.contexts[0]
    assert context.snaps == 42.0
    assert context.weather == {"wind_mph": 8}
    assert context.source == "merged"
    assert context.provenance["snaps"] == "fake-provider"
    assert context.provenance["weather"] == "weather-provider"
    assert result.failures == ()


def test_registry_reports_provider_failure_and_partial_player_missingness() -> None:
    request = ContextRequest(season=2026, week=4, player_ids=["player-1", "player-2"])
    result = ContextProviderRegistry([FailingProvider()]).fetch_context(
        request, required_inputs=[ContextInput.ROUTES]
    )

    assert result.contexts == ()
    assert result.failures[0].code == "provider_failure"
    assert result.missing_inputs[0].code == "provider_error"
    assert result.missing_inputs[0].scope is MissingnessScope.REQUEST


def test_registry_reports_week_and_player_missingness_from_partial_response() -> None:
    request = ContextRequest(season=2026, week=4, player_ids=["player-1", "player-2"])
    result = ContextProviderRegistry([PartialProvider()]).fetch_context(
        request, required_inputs=[ContextInput.ROUTES, ContextInput.TARGET_SHARE]
    )

    missing = {(row.scope, row.player_id, row.input_name) for row in result.missing_inputs}
    assert (MissingnessScope.PLAYER, "player-2", ContextInput.ROUTES) in missing
    assert (MissingnessScope.PLAYER, "player-1", ContextInput.TARGET_SHARE) in missing

    empty = ContextProviderRegistry([EmptyProvider()]).fetch_context(
        ContextRequest(season=2026, week=4), required_inputs=[ContextInput.ROUTES]
    )
    assert empty.missing_inputs[0].scope is MissingnessScope.WEEK


def test_registry_rejects_duplicate_and_mismatched_provenance() -> None:
    try:
        ContextProviderRegistry([FakeContextProvider(), FakeContextProvider()])
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate provider names must be rejected")

    result = ContextProviderRegistry([MismatchedProvider()]).fetch_context(
        ContextRequest(season=2026, week=4, player_ids=["player-1"]),
        required_inputs=[ContextInput.SNAPS],
    )
    assert result.contexts == ()
    assert result.failures[0].code == "provider_failure"
    assert "mismatched source" in result.failures[0].message


def test_registry_duplicate_field_is_reported_without_overwriting_first_value() -> None:
    result = ContextProviderRegistry([FakeContextProvider(), DuplicateProvider()]).fetch_context(
        ContextRequest(season=2026, week=4, player_ids=["player-1"]),
        required_inputs=[ContextInput.SNAPS],
    )

    assert result.contexts[0].snaps == 42.0
    assert result.failures[0].code == "duplicate_context"


def test_registry_result_is_json_safe_for_provider_native_values() -> None:
    result = ContextProviderRegistry([NonJsonProvider()]).fetch_context(
        ContextRequest(season=2026, week=4, player_ids=["player-1"]),
        required_inputs=[ContextInput.WEATHER],
    )

    import json

    encoded = result.json()
    decoded = json.loads(encoded)
    assert decoded["contexts"][0]["weather"] == {
        "as_of": "2026-10-03T12:00:00",
        "not_a_number": None,
        "unknown": "native-value",
    }


def test_backtest_metrics_are_deterministic_and_explainable() -> None:
    metrics = run_backtest(DEFAULT_FIXTURES)

    assert metrics.to_dict() == {
        "rows": 4,
        "bad_adds": 1,
        "spike_flags": 1,
        "spike_flags_prevented_bad_adds": 1,
        "spike_prevention_rate": 1.0,
        "breakouts": 2,
        "role_stability_flags": 2,
        "role_stability_flags_caught_breakouts": 1,
        "role_stability_capture_rate": 0.5,
        "role_change_rows": 2,
        "projection_lag_role_changes": 2,
        "projection_lag_rate": 1.0,
        "rules": {
            "spike_score_threshold": 0.7,
            "role_stability_threshold": 0.7,
            "breakout_multiplier": 1.25,
        },
    }
    assert json.loads(metrics.json()) == metrics.to_dict()


def test_backtest_applies_custom_rules_and_handles_zero_denominators() -> None:
    metrics = run_backtest(
        [BacktestFixture(2025, 1, "p", 10, 11, 0.5, 0.5, 0)],
        rules=CalibrationRules(
            spike_score_threshold=0.4,
            role_stability_threshold=0.4,
            breakout_multiplier=1.5,
        ),
    )

    assert metrics.spike_flags == 1
    assert metrics.role_stability_flags == 1
    assert metrics.spike_prevention_rate == 0.0
    assert metrics.role_stability_capture_rate == 0.0
    assert metrics.projection_lag_rate == 0.0


class FakeContextProvider:
    provider_name = "fake-provider"

    def provided_inputs(self):
        return {"snaps", "route_participation", "target_share"}

    def fetch_context(self, request: ContextRequest) -> ContextProviderResult:
        return ContextProviderResult(
            provider=self.provider_name,
            contexts=[
                EnrichedPlayerContext(
                    season=request.season,
                    week=request.week,
                    player_id=request.player_ids[0],
                    team="DEN",
                    snaps=42.0,
                    route_participation=0.72,
                    target_share=0.24,
                    source=self.provider_name,
                    fetched_at=1000.0,
                )
            ],
            raw_payloads=[
                RawProviderPayload(
                    provider=self.provider_name,
                    source="usage-endpoint",
                    payload={"provider_player_id": "abc", "snap_count": 42},
                    fetched_at=1000.0,
                    metadata={"schema": "provider-native"},
                )
            ],
        )


class WeatherProvider:
    provider_name = "weather-provider"

    def provided_inputs(self):
        return {ContextInput.WEATHER}

    def fetch_context(self, request):
        return ContextProviderResult(
            provider=self.provider_name,
            contexts=[
                EnrichedPlayerContext(
                    season=request.season,
                    week=request.week,
                    player_id="player-1",
                    weather={"wind_mph": 8},
                    source=self.provider_name,
                )
            ],
        )


class FailingProvider:
    provider_name = "failing-provider"

    def provided_inputs(self):
        return {ContextInput.ROUTES}

    def fetch_context(self, request):
        raise RuntimeError("upstream unavailable")


class PartialProvider:
    provider_name = "partial-provider"

    def provided_inputs(self):
        return {ContextInput.ROUTES, ContextInput.TARGET_SHARE}

    def fetch_context(self, request):
        return ContextProviderResult(
            provider=self.provider_name,
            contexts=[
                EnrichedPlayerContext(
                    season=request.season,
                    week=request.week,
                    player_id="player-1",
                    routes=31,
                    source=self.provider_name,
                ),
                EnrichedPlayerContext(
                    season=request.season,
                    week=request.week,
                    player_id="player-2",
                    target_share=0.2,
                    source=self.provider_name,
                ),
            ],
        )


class EmptyProvider(PartialProvider):
    def fetch_context(self, request):
        return ContextProviderResult(provider=self.provider_name)


class MismatchedProvider(FakeContextProvider):
    def fetch_context(self, request):
        result = super().fetch_context(request)
        context = result.contexts[0]
        return ContextProviderResult(
            provider=self.provider_name,
            contexts=[EnrichedPlayerContext(**{**context.__dict__, "source": "other"})],
        )


class DuplicateProvider(FakeContextProvider):
    provider_name = "duplicate-provider"

    def provided_inputs(self):
        return {ContextInput.SNAPS}

    def fetch_context(self, request):
        return ContextProviderResult(
            provider=self.provider_name,
            contexts=[
                EnrichedPlayerContext(
                    season=request.season,
                    week=request.week,
                    player_id="player-1",
                    snaps=99,
                    source=self.provider_name,
                )
            ],
        )


class NonJsonProvider:
    provider_name = "non-json-provider"

    def provided_inputs(self):
        return {ContextInput.WEATHER}

    def fetch_context(self, request):
        from datetime import datetime

        return ContextProviderResult(
            provider=self.provider_name,
            contexts=[
                EnrichedPlayerContext(
                    season=request.season,
                    week=request.week,
                    player_id="player-1",
                    weather={
                        "as_of": datetime(2026, 10, 3, 12),
                        "not_a_number": float("nan"),
                        "unknown": NativeValue(),
                    },
                    source=self.provider_name,
                )
            ],
        )


class NativeValue:
    def __str__(self):
        return "native-value"


# --- provider metric contract (issue #42) -----------------------------------


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


FIXTURE = _fixture()
NOW = FIXTURE["clock"]["now"]
MAX_AGE = FIXTURE["clock"]["max_age_seconds"]
GAMES = {key: GameIdentity.from_dict(value) for key, value in FIXTURE["games"].items()}
LICENSES = {key: ProviderLicense.from_dict(value) for key, value in FIXTURE["licenses"].items()}
RAW_BY_ID = {row["id"]: row for row in FIXTURE["valid"] + FIXTURE["traded"]}


def _observation(row: dict) -> ProviderMetricObservation:
    return ProviderMetricObservation.from_dict(row, games=GAMES, licenses=LICENSES)


def _valid(fixture_id: str) -> ProviderMetricObservation:
    return _observation(RAW_BY_ID[fixture_id])


def _validate(rows, provider: str, week: int = 4):
    return validate_observations(
        rows, provider=provider, season=2026, week=week, now=NOW, max_age_seconds=MAX_AGE
    )


def test_contract_reason_codes_are_explicit() -> None:
    assert NOT_EVALUABLE_MISSING_ROUTES == missing_input_code(ContextInput.ROUTES)
    assert NOT_EVALUABLE_MISSING_PRESSURE == missing_input_code(ContextInput.PRESSURE)
    assert NOT_EVALUABLE_MISSING_WEATHER == missing_input_code(ContextInput.WEATHER)
    assert NOT_EVALUABLE_MISSING_IMPLIED_TOTALS == missing_input_code(
        ContextInput.IMPLIED_TOTALS
    )
    assert {
        PROVIDER_DATA_STALE,
        PROVIDER_IDENTITY_UNRESOLVED,
        PROVIDER_DENOMINATOR_MISSING,
    } <= set(PROVIDER_REASON_CODES)
    assert len(PROVIDER_REASON_CODES) == len(set(PROVIDER_REASON_CODES))


def test_fixture_covers_every_required_metric_family() -> None:
    names = {row["metric_name"] for row in FIXTURE["valid"]}
    inputs = {row.get("context_input") for row in FIXTURE["valid"]}
    assert {"routes_run", "targets_per_route_run", "yards_per_route_run"} <= names
    assert {
        "routes",
        "route_participation",
        "target_share",
        "carry_share",
        "red_zone_use",
        "pressure",
        "pace",
        "weather",
        "implied_totals",
    } <= inputs


@pytest.mark.parametrize("provider", ["mock-commercial", "nflverse"])
def test_valid_fixture_payloads_pass_contract(provider: str) -> None:
    rows = [_observation(row) for row in FIXTURE["valid"] if row["provider"] == provider]
    report = _validate(rows, provider)

    assert report.issues == ()
    assert len(report.accepted) == len(rows)


def test_sample_payload_maps_to_game_team_opponent_player_without_ambiguity() -> None:
    obs = _valid("wr1_route_participation")

    assert (obs.season, obs.game.season_type, obs.week) == (2026, "REG", 4)
    assert obs.game.game_id == "2026_04_DEN_KC"
    assert obs.game.kickoff_utc == datetime.fromisoformat("2026-09-27T20:25:00+00:00")
    assert obs.game.opponent_for(obs.team) == obs.opponent == "KC"
    assert obs.game.home_away_for(obs.team) is HomeAway.AWAY
    assert obs.player.provider_player_id == "mc-1001"
    assert obs.player.gsis_id == "00-0099001"
    assert obs.player_id == "fixture-wr1"
    assert (obs.numerator, obs.denominator) == (30, 34)
    assert obs.denominator_field == "team_dropbacks"
    assert obs.license.mcp_exposure == "derived_only"
    serialized = json.loads(json.dumps(obs.to_dict()))
    assert serialized["game"]["kickoff_utc"] == "2026-09-27T20:25:00+00:00"
    assert serialized["units"] == "ratio"
    assert serialized["scope"] == "player_game"
    assert serialized["revision_status"] == "final"


def test_game_identity_requires_timezone_aware_kickoff() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        GameIdentity(2026, "REG", 4, "2026_04_DEN_KC", datetime(2026, 9, 27, 16, 25), "KC", "DEN")
    with pytest.raises(ValueError, match="season_type"):
        GameIdentity.from_dict({**FIXTURE["games"]["2026_04_DEN_KC"], "season_type": "X"})


def test_traded_player_rows_stay_keyed_by_game_and_team() -> None:
    week3 = _valid("wr2_week3_lv")
    week4 = _valid("wr2_week4_kc")

    assert week3.player_id == week4.player_id == "fixture-wr2"
    assert week3.key() != week4.key()
    assert _validate([week3], "nflverse", week=3).issues == ()
    assert _validate([week4], "nflverse", week=4).issues == ()
    # The week-3 LV row is a week mismatch for a week-4 request, not a merge.
    assert "provider_week_mismatch" in _validate([week3], "nflverse").issue_codes()


@pytest.mark.parametrize("case", FIXTURE["invalid"], ids=lambda case: case["case"])
def test_invalid_fixture_payloads_are_rejected_with_reason_code(case: dict) -> None:
    base = RAW_BY_ID[case["base"]]
    row = _observation({**base, **case["patch"]})

    report = _validate([row], base["provider"])

    assert report.accepted == ()
    assert report.rejected == (row,)
    assert case["expect"] in report.issue_codes()


def test_duplicate_observations_are_all_rejected() -> None:
    first = _valid("wr1_target_share")
    second = _observation({**RAW_BY_ID["wr1_target_share"], "value": 0.28125, "numerator": 9})

    report = _validate([first, second, _valid("rb1_carry_share")], "nflverse")

    assert [row.metric_name for row in report.accepted] == ["carry_share"]
    assert [issue.code for issue in report.issues] == [PROVIDER_DUPLICATE_RECORD] * 2


def test_stale_detection_is_deterministic_and_explicit() -> None:
    obs = _valid("wr1_target_share")

    assert freshness_status(obs, now=NOW, max_age_seconds=MAX_AGE) is FreshnessStatus.FRESH
    assert freshness_status(obs, now=NOW + MAX_AGE + 1, max_age_seconds=MAX_AGE) is (
        FreshnessStatus.STALE
    )
    assert freshness_status(obs, now=None, max_age_seconds=MAX_AGE) is FreshnessStatus.UNKNOWN
    issues = validate_observation(
        obs, provider="nflverse", season=2026, week=4, now=NOW + 10 * MAX_AGE,
        max_age_seconds=MAX_AGE,
    )
    assert [issue.code for issue in issues] == [PROVIDER_DATA_STALE]
    assert issues[0].context_input is ContextInput.TARGET_SHARE


def test_lookup_distinguishes_real_zero_from_null_and_absent_rows() -> None:
    accepted = _validate(
        [
            _valid("te1_target_share_zero"),
            _valid("wr1_target_share"),
        ],
        "nflverse",
    ).accepted
    commercial = _validate([_valid("te1_route_participation_null")], "mock-commercial").accepted

    zero = lookup_observation(
        accepted, metric_name="target_share", game_id="2026_04_DEN_KC",
        player_id="fixture-te1", context_input="target_share",
    )
    assert zero.status is LookupStatus.PRESENT
    assert zero.value == 0.0
    assert zero.missing is None

    null = lookup_observation(
        commercial, metric_name="route_participation", game_id="2026_04_DEN_KC",
        player_id="fixture-te1", context_input=ContextInput.ROUTE_PARTICIPATION,
    )
    assert null.status is LookupStatus.NULL_VALUE
    assert null.value is None
    assert null.missing.code == "not_evaluable_missing_route_participation"
    assert null.missing.scope is MissingnessScope.PLAYER

    absent = lookup_observation(
        accepted, metric_name="routes_run", game_id="2026_04_DEN_KC",
        player_id="fixture-te1", context_input=ContextInput.ROUTES,
    )
    assert absent.status is LookupStatus.ABSENT
    assert absent.observation is None
    assert absent.missing.code == NOT_EVALUABLE_MISSING_ROUTES


def test_provider_computed_model_metrics_require_full_model_metadata() -> None:
    epa = _valid("wr1_receiving_epa")
    xfp = _valid("wr1_xfp")
    assert _validate([epa, xfp], "nflverse").issues == ()
    assert epa.model.missing_fields() == ()
    assert xfp.model.model_version == "v1.0.0"

    bare = _observation({**RAW_BY_ID["wr1_receiving_epa"], "model": None})
    issue = _validate([bare], "nflverse").issues[0]
    assert issue.code == "provider_model_metadata_missing"
    for field_name in ("model_name", "model_version", "sample_size", "provenance_url"):
        assert field_name in issue.message


def test_registry_validates_observations_and_surfaces_reason_codes() -> None:
    request = ContextRequest(season=2026, week=4, player_ids=["fixture-wr1"])
    good = _valid("wr1_target_share")
    stale = _observation({**RAW_BY_ID["rb1_carry_share"], "fetched_at": 1})
    unresolved = _observation(
        {
            **RAW_BY_ID["wr1_red_zone"],
            "player": {"provider": "nflverse", "provider_player_id": "00-0099999"},
        }
    )
    no_denominator = _observation({**RAW_BY_ID["den_neutral_pace"], "denominator": None})

    class ObservationProvider:
        provider_name = "nflverse"

        def provided_inputs(self):
            return {ContextInput.TARGET_SHARE}

        def fetch_context(self, request):
            return ContextProviderResult(
                provider=self.provider_name,
                observations=[good, stale, unresolved, no_denominator],
            )

    result = ContextProviderRegistry(
        [ObservationProvider()], now=NOW, max_age_seconds=MAX_AGE
    ).fetch_context(request)

    assert result.observations == (good,)
    codes = {(row.input_name, row.code) for row in result.missing_inputs}
    assert (ContextInput.CARRY_SHARE, PROVIDER_DATA_STALE) in codes
    assert (ContextInput.RED_ZONE_USE, PROVIDER_IDENTITY_UNRESOLVED) in codes
    assert (ContextInput.PACE, PROVIDER_DENOMINATOR_MISSING) in codes
    assert result.failures == ()
    decoded = json.loads(result.json())
    assert decoded["observations"][0]["metric_name"] == "target_share"
    assert {issue["code"] for issue in decoded["observation_issues"]} == {
        PROVIDER_DATA_STALE,
        PROVIDER_IDENTITY_UNRESOLVED,
        PROVIDER_DENOMINATOR_MISSING,
    }
    # Observations are audit-only: they never populate scored context fields.
    assert result.contexts == ()


def test_observation_scope_is_game_level_for_weather() -> None:
    wind = _valid("game_wind")
    assert wind.scope is MetricScope.GAME
    assert wind.team is None
    assert _validate([wind], "nflverse").issues == ()
