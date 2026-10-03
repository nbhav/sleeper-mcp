from __future__ import annotations

import json
import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol


class ContextInput(StrEnum):
    SNAPS = "snaps"
    ROUTES = "routes"
    ROUTE_PARTICIPATION = "route_participation"
    TARGET_SHARE = "target_share"
    CARRY_SHARE = "carry_share"
    RED_ZONE_USE = "red_zone_use"
    AIR_YARDS = "air_yards"
    PRESSURE = "pressure"
    PACE = "pace"
    IMPLIED_TOTALS = "implied_totals"
    WEATHER = "weather"


OPTIONAL_CONTEXT_INPUTS: tuple[ContextInput, ...] = tuple(ContextInput)


class MissingnessScope(StrEnum):
    REQUEST = "request"
    WEEK = "week"
    PLAYER = "player"


@dataclass(frozen=True)
class ContextRequest:
    season: int
    week: int
    player_ids: Sequence[str] = ()
    teams: Sequence[str] = ()
    league_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.season < 1 or self.week < 1:
            raise ValueError("season and week must be positive")


@dataclass(frozen=True)
class EnrichedPlayerContext:
    season: int
    week: int
    player_id: str
    team: str | None = None
    opponent: str | None = None
    snaps: float | None = None
    routes: float | None = None
    route_participation: float | None = None
    target_share: float | None = None
    carry_share: float | None = None
    red_zone_use: float | None = None
    air_yards: float | None = None
    pressure: float | None = None
    pace: float | None = None
    implied_totals: float | None = None
    weather: Mapping[str, Any] | None = None
    source: str | None = None
    fetched_at: float | None = None
    provenance: Mapping[str, str] = field(default_factory=dict)

    def provided_inputs(self) -> frozenset[ContextInput]:
        return frozenset(
            context_input
            for context_input in ContextInput
            if getattr(self, context_input.value) is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "season": self.season,
                "week": self.week,
                "player_id": self.player_id,
                "team": self.team,
                "opponent": self.opponent,
                "snaps": self.snaps,
                "routes": self.routes,
                "route_participation": self.route_participation,
                "target_share": self.target_share,
                "carry_share": self.carry_share,
                "red_zone_use": self.red_zone_use,
                "air_yards": self.air_yards,
                "pressure": self.pressure,
                "pace": self.pace,
                "implied_totals": self.implied_totals,
                "weather": self.weather,
                "source": self.source,
                "fetched_at": self.fetched_at,
                "provenance": dict(self.provenance),
                "provided_inputs": sorted(
                    input_name.value for input_name in self.provided_inputs()
                ),
            }
        )


@dataclass(frozen=True)
class RawProviderPayload:
    provider: str
    source: str
    payload: Any
    fetched_at: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "provider": self.provider,
                "source": self.source,
                "payload": self.payload,
                "fetched_at": self.fetched_at,
                "metadata": dict(self.metadata),
            }
        )


@dataclass(frozen=True)
class MissingContextInput:
    input_name: ContextInput
    code: str
    scope: MissingnessScope = MissingnessScope.REQUEST
    season: int | None = None
    week: int | None = None
    player_id: str | None = None
    provider: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "input": self.input_name.value,
                "code": self.code,
                "scope": self.scope.value,
                "season": self.season,
                "week": self.week,
                "player_id": self.player_id,
                "provider": self.provider,
                "reason": self.reason,
            }
        )


@dataclass(frozen=True)
class ProviderFailure:
    provider: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class ContextProviderResult:
    provider: str
    contexts: Sequence[EnrichedPlayerContext] = ()
    raw_payloads: Sequence[RawProviderPayload] = ()
    missing_inputs: Sequence[MissingContextInput] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "provider": self.provider,
                "contexts": [context.to_dict() for context in self.contexts],
                "raw_payloads": [payload.to_dict() for payload in self.raw_payloads],
                "missing_inputs": [missing.to_dict() for missing in self.missing_inputs],
                "metadata": dict(self.metadata),
            }
        )


@dataclass(frozen=True)
class RegistryContextResult:
    request: ContextRequest
    contexts: Sequence[EnrichedPlayerContext] = ()
    raw_payloads: Sequence[RawProviderPayload] = ()
    missing_inputs: Sequence[MissingContextInput] = ()
    failures: Sequence[ProviderFailure] = ()

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "request": asdict(self.request),
                "contexts": [context.to_dict() for context in self.contexts],
                "raw_payloads": [payload.to_dict() for payload in self.raw_payloads],
                "missing_inputs": [missing.to_dict() for missing in self.missing_inputs],
                "failures": [failure.to_dict() for failure in self.failures],
            }
        )

    def json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


class EnrichedContextProvider(Protocol):
    provider_name: str

    def provided_inputs(self) -> Collection[ContextInput | str]:
        ...

    def fetch_context(self, request: ContextRequest) -> ContextProviderResult:
        ...


class ContextProviderRegistry:
    def __init__(self, providers: Iterable[EnrichedContextProvider] = ()) -> None:
        self.providers = tuple(providers)
        names = [provider.provider_name for provider in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("provider_name values must be unique")

    def provided_inputs(self) -> frozenset[ContextInput]:
        inputs: set[ContextInput] = set()
        for provider in self.providers:
            inputs.update(
                normalize_context_input(input_name)
                for input_name in provider.provided_inputs()
            )
        return frozenset(inputs)

    def missing_inputs(
        self,
        required_inputs: Iterable[ContextInput | str],
        *,
        player_id: str | None = None,
    ) -> list[MissingContextInput]:
        provided = self.provided_inputs()
        return [
            MissingContextInput(
                input_name=input_name,
                code=missing_input_code(input_name),
                scope=MissingnessScope.PLAYER if player_id else MissingnessScope.REQUEST,
                player_id=player_id,
                reason=(
                    "no configured context provider advertises "
                    f"{input_name.value}"
                ),
            )
            for input_name in sorted(
                {normalize_context_input(value) for value in required_inputs},
                key=lambda value: value.value,
            )
            if input_name not in provided
        ]

    def fetch_context(
        self,
        request: ContextRequest,
        *,
        required_inputs: Iterable[ContextInput | str] = (),
    ) -> RegistryContextResult:
        required = tuple(
            sorted(
                {normalize_context_input(value) for value in required_inputs},
                key=lambda value: value.value,
            )
        )
        contexts: dict[str, EnrichedPlayerContext] = {}
        raw_payloads: list[RawProviderPayload] = []
        missing: list[MissingContextInput] = []
        failures: list[ProviderFailure] = []
        advertised = self.provided_inputs()

        for input_name in required:
            if input_name not in advertised:
                missing.append(
                    MissingContextInput(
                        input_name,
                        missing_input_code(input_name),
                        scope=MissingnessScope.REQUEST,
                        season=request.season,
                        week=request.week,
                        reason=f"no provider advertises {input_name.value}",
                    )
                )

        for provider in self.providers:
            provider_inputs = {
                normalize_context_input(value) for value in provider.provided_inputs()
            }
            try:
                result = provider.fetch_context(request)
                self._validate_result_provenance(provider, request, result)
            except Exception as exc:  # isolate provider failures from other providers
                failures.append(
                    ProviderFailure(
                        provider=provider.provider_name,
                        code="provider_failure",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
                for input_name in sorted(provider_inputs & set(required), key=lambda x: x.value):
                    missing.append(
                        MissingContextInput(
                            input_name,
                            "provider_error",
                            scope=MissingnessScope.REQUEST,
                            season=request.season,
                            week=request.week,
                            provider=provider.provider_name,
                            reason="provider failed while fetching context",
                        )
                    )
                continue

            raw_payloads.extend(result.raw_payloads)
            for context in result.contexts:
                existing = contexts.get(context.player_id)
                if existing is None:
                    contexts[context.player_id] = _with_provenance(
                        context, provider.provider_name
                    )
                    continue
                try:
                    contexts[context.player_id] = _merge_contexts(
                        existing, context, provider.provider_name
                    )
                except ValueError as exc:
                    failures.append(
                        ProviderFailure(provider.provider_name, "duplicate_context", str(exc))
                    )

            missing.extend(result.missing_inputs)

        if not contexts:
            for input_name in required:
                if input_name in advertised and not any(
                    row.input_name == input_name for row in missing
                ):
                    missing.append(
                        MissingContextInput(
                            input_name,
                            missing_input_code(input_name),
                            scope=MissingnessScope.WEEK,
                            season=request.season,
                            week=request.week,
                            reason="no provider returned context for the requested week",
                        )
                    )
        elif request.player_ids:
            for player_id in request.player_ids:
                context = contexts.get(player_id)
                for input_name in required:
                    if context is not None and getattr(context, input_name.value) is not None:
                        continue
                    if any(
                        row.input_name == input_name and row.player_id == player_id
                        for row in missing
                    ):
                        continue
                    missing.append(
                        MissingContextInput(
                            input_name,
                            missing_input_code(input_name),
                            scope=MissingnessScope.PLAYER,
                            season=request.season,
                            week=request.week,
                            player_id=player_id,
                            reason="provider returned no value for this player and week",
                        )
                    )

        return RegistryContextResult(
            request=request,
            contexts=tuple(contexts[player_id] for player_id in sorted(contexts)),
            raw_payloads=tuple(raw_payloads),
            missing_inputs=tuple(missing),
            failures=tuple(failures),
        )

    @staticmethod
    def _validate_result_provenance(
        provider: EnrichedContextProvider,
        request: ContextRequest,
        result: ContextProviderResult,
    ) -> None:
        if result.provider != provider.provider_name:
            raise ValueError(
                f"result provider {result.provider!r} does not match "
                f"{provider.provider_name!r}"
            )
        for context in result.contexts:
            if (context.season, context.week) != (request.season, request.week):
                raise ValueError("provider returned context for a different season or week")
            if request.player_ids and context.player_id not in request.player_ids:
                raise ValueError(f"provider returned unrequested player {context.player_id!r}")
            if context.source != provider.provider_name:
                raise ValueError(
                    f"context {context.player_id!r} has mismatched source "
                    f"{context.source!r}"
                )
        for payload in result.raw_payloads:
            if payload.provider != provider.provider_name:
                raise ValueError(
                    f"raw payload has mismatched provider {payload.provider!r}"
                )


def _with_provenance(context: EnrichedPlayerContext, provider: str) -> EnrichedPlayerContext:
    return EnrichedPlayerContext(
        **{
            **asdict(context),
            "provenance": {
                **dict(context.provenance),
                **{input_name.value: provider for input_name in context.provided_inputs()},
            },
        }
    )


def _merge_contexts(
    left: EnrichedPlayerContext,
    right: EnrichedPlayerContext,
    provider: str,
) -> EnrichedPlayerContext:
    if (left.season, left.week, left.player_id) != (right.season, right.week, right.player_id):
        raise ValueError("contexts have mismatched season, week, or player")
    values = asdict(left)
    output_provenance = dict(left.provenance)
    for input_name in ContextInput:
        value = getattr(right, input_name.value)
        if value is None:
            continue
        if getattr(left, input_name.value) is not None:
            raise ValueError(
                f"duplicate value for {input_name.value} on player {left.player_id}"
            )
        values[input_name.value] = value
        output_provenance[input_name.value] = provider
    for field_name in ("team", "opponent", "fetched_at"):
        if values[field_name] is None:
            values[field_name] = getattr(right, field_name)
    values["source"] = left.source if left.source == right.source else "merged"
    values["provenance"] = output_provenance
    return EnrichedPlayerContext(**values)


def missing_input_code(input_name: ContextInput | str) -> str:
    return f"not_evaluable_missing_{normalize_context_input(input_name).value}"


def normalize_context_input(input_name: ContextInput | str) -> ContextInput:
    if isinstance(input_name, ContextInput):
        return input_name
    normalized = str(input_name).strip().lower().replace("-", "_").replace(" ", "_")
    return ContextInput(normalized)


def json_safe(value: Any) -> Any:
    """Convert provider-native values into values accepted by ``json.dumps``."""
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [json_safe(item) for item in sorted(value, key=repr)]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
