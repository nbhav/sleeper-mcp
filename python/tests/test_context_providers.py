from __future__ import annotations

import json

from sleeper_tooling.context_backtest import (
    BacktestFixture,
    CalibrationRules,
    DEFAULT_FIXTURES,
    run_backtest,
)
from sleeper_tooling.context_providers import (
    ContextInput,
    ContextProviderRegistry,
    ContextProviderResult,
    ContextRequest,
    EnrichedPlayerContext,
    MissingnessScope,
    RawProviderPayload,
    missing_input_code,
)


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
