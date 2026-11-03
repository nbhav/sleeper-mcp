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


# ---------------------------------------------------------------------------
# Provider metric contract (issue #42)
#
# The types below describe one provider-native metric value with enough
# identity, denominator, timestamp, revision, licensing, and model metadata to
# be audited before anything downstream may use it. They are deliberately not
# consumed by scoring or recommendations; see docs/provider-contract-research.md.
# ---------------------------------------------------------------------------

NOT_EVALUABLE_MISSING_ROUTES = "not_evaluable_missing_routes"
NOT_EVALUABLE_MISSING_PRESSURE = "not_evaluable_missing_pressure"
NOT_EVALUABLE_MISSING_WEATHER = "not_evaluable_missing_weather"
NOT_EVALUABLE_MISSING_IMPLIED_TOTALS = "not_evaluable_missing_implied_totals"

PROVIDER_DATA_STALE = "provider_data_stale"
PROVIDER_IDENTITY_UNRESOLVED = "provider_identity_unresolved"
PROVIDER_DENOMINATOR_MISSING = "provider_denominator_missing"
PROVIDER_NUMERATOR_MISSING = "provider_numerator_missing"
PROVIDER_DEFINITION_MISSING = "provider_definition_missing"
PROVIDER_INVALID_PERCENTAGE = "provider_invalid_percentage"
PROVIDER_RATIO_MISMATCH = "provider_ratio_mismatch"
PROVIDER_WEEK_MISMATCH = "provider_week_mismatch"
PROVIDER_GAME_MISMATCH = "provider_game_mismatch"
PROVIDER_GAME_NOT_PLAYED = "provider_game_not_played"
PROVIDER_SOURCE_MISMATCH = "provider_source_mismatch"
PROVIDER_DUPLICATE_RECORD = "provider_duplicate_record"
PROVIDER_PROVENANCE_MISSING = "provider_provenance_missing"
PROVIDER_MODEL_METADATA_MISSING = "provider_model_metadata_missing"

PROVIDER_REASON_CODES: tuple[str, ...] = (
    PROVIDER_DATA_STALE,
    PROVIDER_IDENTITY_UNRESOLVED,
    PROVIDER_DENOMINATOR_MISSING,
    PROVIDER_NUMERATOR_MISSING,
    PROVIDER_DEFINITION_MISSING,
    PROVIDER_INVALID_PERCENTAGE,
    PROVIDER_RATIO_MISMATCH,
    PROVIDER_WEEK_MISMATCH,
    PROVIDER_GAME_MISMATCH,
    PROVIDER_GAME_NOT_PLAYED,
    PROVIDER_SOURCE_MISMATCH,
    PROVIDER_DUPLICATE_RECORD,
    PROVIDER_PROVENANCE_MISSING,
    PROVIDER_MODEL_METADATA_MISSING,
)

# Provider-computed model outputs that must carry model/version/sample/provenance.
MODEL_METRIC_FAMILIES: frozenset[str] = frozenset({"epa", "dvoa", "xfp", "war"})


class MetricScope(StrEnum):
    PLAYER_GAME = "player_game"
    TEAM_GAME = "team_game"
    GAME = "game"


class MetricUnit(StrEnum):
    COUNT = "count"
    RATIO = "ratio"  # bounded fraction in [0, 1]
    PERCENT = "percent"  # bounded percentage in [0, 100]
    RATE = "rate"  # unbounded numerator / denominator, e.g. yards per route
    YARDS = "yards"
    POINTS = "points"
    SECONDS = "seconds"
    SECONDS_PER_PLAY = "seconds_per_play"
    MPH = "mph"
    FAHRENHEIT = "fahrenheit"
    EXPECTED_POINTS = "expected_points"
    INDEX = "index"  # unbounded model score, e.g. DVOA or WAR
    CATEGORY = "category"


RATIO_UNITS: frozenset[MetricUnit] = frozenset(
    {MetricUnit.RATIO, MetricUnit.PERCENT, MetricUnit.RATE, MetricUnit.SECONDS_PER_PLAY}
)


class RevisionStatus(StrEnum):
    PRELIMINARY = "preliminary"
    FINAL = "final"
    CORRECTED = "corrected"
    BACKFILLED = "backfilled"
    UNKNOWN = "unknown"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELED = "canceled"


class HomeAway(StrEnum):
    HOME = "home"
    AWAY = "away"


SEASON_TYPES: frozenset[str] = frozenset({"PRE", "REG", "POST"})


@dataclass(frozen=True)
class GameIdentity:
    """NFL game identity. ``kickoff_utc`` must be timezone-aware.

    ``game_id`` is the canonical (nflverse-style ``{season}_{week}_{away}_{home}``)
    identifier; provider-specific game ids live in ``provider_game_ids``.
    """

    season: int
    season_type: str
    week: int
    game_id: str
    kickoff_utc: datetime
    home_team: str
    away_team: str
    status: GameStatus = GameStatus.SCHEDULED
    neutral_site: bool = False
    venue_id: str | None = None
    roof: str | None = None
    original_kickoff_utc: datetime | None = None
    provider_game_ids: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.season < 1 or self.week < 1:
            raise ValueError("season and week must be positive")
        if self.season_type not in SEASON_TYPES:
            raise ValueError(f"season_type must be one of {sorted(SEASON_TYPES)}")
        if not self.game_id:
            raise ValueError("game_id is required")
        if self.home_team == self.away_team:
            raise ValueError("home_team and away_team must differ")
        for name in ("kickoff_utc", "original_kickoff_utc"):
            value = getattr(self, name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")

    def opponent_for(self, team: str) -> str | None:
        if team == self.home_team:
            return self.away_team
        if team == self.away_team:
            return self.home_team
        return None

    def home_away_for(self, team: str) -> HomeAway | None:
        if team == self.home_team:
            return HomeAway.HOME
        if team == self.away_team:
            return HomeAway.AWAY
        return None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GameIdentity:
        return cls(
            season=int(data["season"]),
            season_type=str(data["season_type"]),
            week=int(data["week"]),
            game_id=str(data["game_id"]),
            kickoff_utc=_parse_datetime(data["kickoff_utc"]),
            home_team=str(data["home_team"]),
            away_team=str(data["away_team"]),
            status=GameStatus(data.get("status", GameStatus.SCHEDULED.value)),
            neutral_site=bool(data.get("neutral_site", False)),
            venue_id=data.get("venue_id"),
            roof=data.get("roof"),
            original_kickoff_utc=(
                _parse_datetime(data["original_kickoff_utc"])
                if data.get("original_kickoff_utc")
                else None
            ),
            provider_game_ids=dict(data.get("provider_game_ids", {})),
        )


@dataclass(frozen=True)
class PlayerIdentity:
    """Provider player id with its resolution path to a Sleeper player_id."""

    provider: str
    provider_player_id: str
    gsis_id: str | None = None
    sleeper_player_id: str | None = None
    id_path: str | None = None  # e.g. "provider->gsis->sleeper"

    @property
    def resolved(self) -> bool:
        return bool(self.sleeper_player_id)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlayerIdentity:
        return cls(
            provider=str(data["provider"]),
            provider_player_id=str(data["provider_player_id"]),
            gsis_id=data.get("gsis_id"),
            sleeper_player_id=data.get("sleeper_player_id"),
            id_path=data.get("id_path"),
        )


@dataclass(frozen=True)
class ProviderLicense:
    name: str
    attribution: str
    url: str | None = None
    storage_allowed: bool | None = None  # None means unverified
    redistribution_allowed: bool | None = None
    mcp_exposure: str = "unverified"  # raw | derived_only | none | unverified

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProviderLicense:
        return cls(**{key: data[key] for key in data if key in _LICENSE_FIELDS})


_LICENSE_FIELDS = frozenset(
    {"name", "attribution", "url", "storage_allowed", "redistribution_allowed", "mcp_exposure"}
)


@dataclass(frozen=True)
class ProviderModelMetadata:
    """Required for provider-computed EPA, DVOA, xFP, and WAR values."""

    model_name: str | None = None
    model_version: str | None = None
    sample_size: int | None = None
    sample_scope: str | None = None
    provenance_url: str | None = None
    calculated_by: str = "provider"  # provider | local

    def missing_fields(self) -> tuple[str, ...]:
        missing = [
            name
            for name in ("model_name", "model_version", "sample_scope", "provenance_url")
            if not getattr(self, name)
        ]
        if self.sample_size is None or self.sample_size <= 0:
            missing.append("sample_size")
        return tuple(sorted(missing))


@dataclass(frozen=True)
class ProviderMetricObservation:
    """One provider-native metric value keyed to an unambiguous NFL game.

    ``value=None`` means the provider row/key exists without a value; a real
    zero is ``value=0`` with its numerator/denominator. An absent row is simply
    not present in the observation list.
    """

    provider: str
    dataset: str
    metric_name: str
    value: float | str | None
    units: MetricUnit
    scope: MetricScope
    game: GameIdentity
    team: str | None = None
    opponent: str | None = None
    home_away: HomeAway | None = None
    player: PlayerIdentity | None = None
    context_input: ContextInput | None = None
    numerator: float | None = None
    denominator: float | None = None
    numerator_field: str | None = None
    denominator_field: str | None = None
    definition: str | None = None
    calculation_version: str | None = None
    model_family: str | None = None
    model: ProviderModelMetadata | None = None
    source_record_id: str | None = None
    source_url: str | None = None
    source_updated_at: float | None = None
    fetched_at: float | None = None
    revision_status: RevisionStatus = RevisionStatus.UNKNOWN
    license: ProviderLicense | None = None
    raw_payload: RawProviderPayload | None = None

    @property
    def season(self) -> int:
        return self.game.season

    @property
    def week(self) -> int:
        return self.game.week

    @property
    def player_id(self) -> str | None:
        return self.player.sleeper_player_id if self.player else None

    def key(self) -> tuple[Any, ...]:
        """Identity used for duplicate detection.

        Includes ``game_id`` and ``team`` so traded players and multi-team
        seasons never collapse into one player/week row.
        """
        player_key = (
            (self.player.provider, self.player.provider_player_id) if self.player else None
        )
        return (
            self.provider,
            self.dataset,
            self.metric_name,
            self.game.season,
            self.game.season_type,
            self.game.week,
            self.game.game_id,
            self.team,
            player_key,
            self.scope.value,
        )

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        games: Mapping[str, GameIdentity] | None = None,
        licenses: Mapping[str, ProviderLicense] | None = None,
    ) -> ProviderMetricObservation:
        """Build from a JSON-safe mapping.

        ``game`` and ``license`` may be inline mappings or string references
        into ``games`` / ``licenses``.
        """
        game_ref = data["game"]
        game = (
            (games or {})[game_ref]
            if isinstance(game_ref, str)
            else GameIdentity.from_dict(game_ref)
        )
        license_ref = data.get("license")
        license_value = (
            (licenses or {})[license_ref]
            if isinstance(license_ref, str)
            else ProviderLicense.from_dict(license_ref)
            if license_ref
            else None
        )
        raw = data.get("raw_payload")
        return cls(
            provider=str(data["provider"]),
            dataset=str(data["dataset"]),
            metric_name=str(data["metric_name"]),
            value=data.get("value"),
            units=MetricUnit(data["units"]),
            scope=MetricScope(data["scope"]),
            game=game,
            team=data.get("team"),
            opponent=data.get("opponent"),
            home_away=HomeAway(data["home_away"]) if data.get("home_away") else None,
            player=PlayerIdentity.from_dict(data["player"]) if data.get("player") else None,
            context_input=(
                normalize_context_input(data["context_input"])
                if data.get("context_input")
                else None
            ),
            numerator=data.get("numerator"),
            denominator=data.get("denominator"),
            numerator_field=data.get("numerator_field"),
            denominator_field=data.get("denominator_field"),
            definition=data.get("definition"),
            calculation_version=data.get("calculation_version"),
            model_family=data.get("model_family"),
            model=ProviderModelMetadata(**data["model"]) if data.get("model") else None,
            source_record_id=data.get("source_record_id"),
            source_url=data.get("source_url"),
            source_updated_at=data.get("source_updated_at"),
            fetched_at=data.get("fetched_at"),
            revision_status=RevisionStatus(
                data.get("revision_status", RevisionStatus.UNKNOWN.value)
            ),
            license=license_value,
            raw_payload=RawProviderPayload(**raw) if raw else None,
        )


@dataclass(frozen=True)
class ProviderValidationIssue:
    code: str
    message: str
    provider: str | None = None
    metric_name: str | None = None
    game_id: str | None = None
    team: str | None = None
    player_id: str | None = None
    context_input: ContextInput | None = None

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


@dataclass(frozen=True)
class ProviderContractReport:
    accepted: Sequence[ProviderMetricObservation] = ()
    rejected: Sequence[ProviderMetricObservation] = ()
    issues: Sequence[ProviderValidationIssue] = ()

    def issue_codes(self) -> frozenset[str]:
        return frozenset(issue.code for issue in self.issues)

    def missing_inputs(self, season: int | None = None, week: int | None = None) -> list[MissingContextInput]:
        """Translate issues on rejected observations into explicit reason codes."""
        return [
            MissingContextInput(
                input_name=issue.context_input,
                code=issue.code,
                scope=MissingnessScope.PLAYER if issue.player_id else MissingnessScope.WEEK,
                season=season,
                week=week,
                player_id=issue.player_id,
                provider=issue.provider,
                reason=issue.message,
            )
            for issue in self.issues
            if issue.context_input is not None
        ]

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "accepted": [row.to_dict() for row in self.accepted],
                "rejected": [row.to_dict() for row in self.rejected],
                "issues": [issue.to_dict() for issue in self.issues],
            }
        )


def freshness_status(
    observation: ProviderMetricObservation,
    *,
    now: float | None,
    max_age_seconds: float | None,
) -> FreshnessStatus:
    """Deterministic freshness: callers pass ``now``; nothing reads the clock."""
    if now is None or max_age_seconds is None or observation.fetched_at is None:
        return FreshnessStatus.UNKNOWN
    age = now - observation.fetched_at
    return FreshnessStatus.STALE if age > max_age_seconds else FreshnessStatus.FRESH


def validate_observation(
    observation: ProviderMetricObservation,
    *,
    provider: str,
    season: int,
    week: int,
    season_type: str = "REG",
    now: float | None = None,
    max_age_seconds: float | None = None,
    tolerance: float = 1e-3,
) -> tuple[ProviderValidationIssue, ...]:
    """Return every contract violation for one observation (empty if valid)."""
    issues: list[ProviderValidationIssue] = []
    obs = observation
    game = obs.game

    def add(code: str, message: str) -> None:
        issues.append(
            ProviderValidationIssue(
                code=code,
                message=message,
                provider=obs.provider,
                metric_name=obs.metric_name,
                game_id=game.game_id,
                team=obs.team,
                player_id=obs.player_id,
                context_input=obs.context_input,
            )
        )

    if obs.provider != provider:
        add(PROVIDER_SOURCE_MISMATCH, f"observation provider {obs.provider!r} is not {provider!r}")
    if obs.raw_payload is not None and obs.raw_payload.provider != obs.provider:
        add(
            PROVIDER_SOURCE_MISMATCH,
            f"raw payload provider {obs.raw_payload.provider!r} is not {obs.provider!r}",
        )

    if (game.season, game.season_type, game.week) != (season, season_type, week):
        add(
            PROVIDER_WEEK_MISMATCH,
            f"game {game.game_id} is {game.season} {game.season_type} week {game.week}, "
            f"expected {season} {season_type} week {week}",
        )

    if obs.scope is not MetricScope.GAME or obs.team is not None:
        if obs.team is None:
            add(PROVIDER_GAME_MISMATCH, f"{obs.scope.value} observation has no team")
        elif game.opponent_for(obs.team) is None:
            add(PROVIDER_GAME_MISMATCH, f"team {obs.team} is not in game {game.game_id}")
        else:
            if obs.opponent is not None and obs.opponent != game.opponent_for(obs.team):
                add(
                    PROVIDER_GAME_MISMATCH,
                    f"opponent {obs.opponent} does not match game {game.game_id}",
                )
            if obs.home_away is not None and obs.home_away != game.home_away_for(obs.team):
                add(
                    PROVIDER_GAME_MISMATCH,
                    f"home_away {obs.home_away.value} does not match game {game.game_id}",
                )

    if obs.scope is not MetricScope.GAME and game.status in (
        GameStatus.POSTPONED,
        GameStatus.CANCELED,
    ):
        add(
            PROVIDER_GAME_NOT_PLAYED,
            f"game {game.game_id} is {game.status.value}; observed stats are not attributable",
        )

    if obs.scope is MetricScope.PLAYER_GAME and (obs.player is None or not obs.player.resolved):
        add(
            PROVIDER_IDENTITY_UNRESOLVED,
            "provider player id does not resolve to a Sleeper player_id"
            if obs.player
            else "player observation has no player identity",
        )

    if not (obs.source_record_id or obs.source_url) or obs.fetched_at is None:
        add(
            PROVIDER_PROVENANCE_MISSING,
            "source_record_id/source_url and fetched_at are required",
        )

    if obs.value is not None and obs.units in RATIO_UNITS:
        _validate_ratio(obs, add, tolerance)

    if _is_model_metric(obs):
        model_missing = (
            obs.model.missing_fields()
            if obs.model is not None
            else ("model_name", "model_version", "provenance_url", "sample_scope", "sample_size")
        )
        if model_missing:
            add(
                PROVIDER_MODEL_METADATA_MISSING,
                f"model metric {obs.metric_name} lacks {', '.join(model_missing)}",
            )

    if freshness_status(obs, now=now, max_age_seconds=max_age_seconds) is FreshnessStatus.STALE:
        add(
            PROVIDER_DATA_STALE,
            f"fetched_at {obs.fetched_at} is older than {max_age_seconds}s at {now}",
        )
    return tuple(issues)


def _validate_ratio(obs: ProviderMetricObservation, add: Any, tolerance: float) -> None:
    if obs.denominator is None or obs.denominator <= 0:
        add(
            PROVIDER_DENOMINATOR_MISSING,
            f"{obs.metric_name} ratio requires a positive denominator"
            + (f" ({obs.denominator_field})" if obs.denominator_field else ""),
        )
    if obs.numerator is None:
        add(PROVIDER_NUMERATOR_MISSING, f"{obs.metric_name} ratio requires a numerator")
    if not obs.definition:
        add(PROVIDER_DEFINITION_MISSING, f"{obs.metric_name} ratio requires a definition")
    try:
        value = float(obs.value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        add(PROVIDER_INVALID_PERCENTAGE, f"{obs.metric_name} value {obs.value!r} is not numeric")
        return
    if not math.isfinite(value):
        add(PROVIDER_INVALID_PERCENTAGE, f"{obs.metric_name} value is not finite")
        return
    bounds = {MetricUnit.RATIO: 1.0, MetricUnit.PERCENT: 100.0}.get(obs.units)
    if bounds is not None and not 0.0 <= value <= bounds:
        add(
            PROVIDER_INVALID_PERCENTAGE,
            f"{obs.metric_name} {obs.units.value} value {value} is outside [0, {bounds:g}]",
        )
        return
    if value < 0:
        add(PROVIDER_INVALID_PERCENTAGE, f"{obs.metric_name} value {value} is negative")
        return
    if obs.numerator is not None and obs.denominator:
        expected = obs.numerator / obs.denominator
        if obs.units is MetricUnit.PERCENT:
            expected *= 100.0
        if abs(expected - value) > tolerance * max(1.0, abs(expected)):
            add(
                PROVIDER_RATIO_MISMATCH,
                f"{obs.metric_name} value {value} != {obs.numerator}/{obs.denominator}",
            )


def _is_model_metric(obs: ProviderMetricObservation) -> bool:
    family = (obs.model_family or "").lower()
    if family in MODEL_METRIC_FAMILIES:
        return obs.model is None or obs.model.calculated_by == "provider"
    return False


def validate_observations(
    observations: Iterable[ProviderMetricObservation],
    *,
    provider: str,
    season: int,
    week: int,
    season_type: str = "REG",
    now: float | None = None,
    max_age_seconds: float | None = None,
    tolerance: float = 1e-3,
) -> ProviderContractReport:
    """Validate a batch; every row sharing a duplicated key is rejected."""
    rows = tuple(observations)
    counts: dict[tuple[Any, ...], int] = {}
    for row in rows:
        counts[row.key()] = counts.get(row.key(), 0) + 1
    accepted: list[ProviderMetricObservation] = []
    rejected: list[ProviderMetricObservation] = []
    issues: list[ProviderValidationIssue] = []
    for row in rows:
        row_issues = list(
            validate_observation(
                row,
                provider=provider,
                season=season,
                week=week,
                season_type=season_type,
                now=now,
                max_age_seconds=max_age_seconds,
                tolerance=tolerance,
            )
        )
        if counts[row.key()] > 1:
            row_issues.append(
                ProviderValidationIssue(
                    code=PROVIDER_DUPLICATE_RECORD,
                    message=f"{counts[row.key()]} rows share key for {row.metric_name}",
                    provider=row.provider,
                    metric_name=row.metric_name,
                    game_id=row.game.game_id,
                    team=row.team,
                    player_id=row.player_id,
                    context_input=row.context_input,
                )
            )
        if row_issues:
            rejected.append(row)
            issues.extend(row_issues)
        else:
            accepted.append(row)
    return ProviderContractReport(tuple(accepted), tuple(rejected), tuple(issues))


class LookupStatus(StrEnum):
    PRESENT = "present"  # row and value exist; value may be a real zero
    NULL_VALUE = "null_value"  # row/key exists, value absent
    ABSENT = "absent"  # no row for this key


@dataclass(frozen=True)
class ObservationLookup:
    status: LookupStatus
    observation: ProviderMetricObservation | None = None
    missing: MissingContextInput | None = None

    @property
    def value(self) -> float | str | None:
        return self.observation.value if self.observation else None


def lookup_observation(
    observations: Iterable[ProviderMetricObservation],
    *,
    metric_name: str,
    game_id: str,
    team: str | None = None,
    player_id: str | None = None,
    context_input: ContextInput | str | None = None,
) -> ObservationLookup:
    """Find one accepted observation, keeping real zeros distinct from absence."""
    matches = [
        row
        for row in observations
        if row.metric_name == metric_name
        and row.game.game_id == game_id
        and (team is None or row.team == team)
        and (player_id is None or row.player_id == player_id)
    ]
    if len(matches) > 1:
        raise ValueError(f"ambiguous lookup for {metric_name} in {game_id}; validate first")
    input_name = normalize_context_input(context_input) if context_input else None

    def missing(reason: str, season: int | None, week: int | None) -> MissingContextInput | None:
        if input_name is None:
            return None
        return MissingContextInput(
            input_name,
            missing_input_code(input_name),
            scope=MissingnessScope.PLAYER if player_id else MissingnessScope.WEEK,
            season=season,
            week=week,
            player_id=player_id,
            provider=matches[0].provider if matches else None,
            reason=reason,
        )

    if not matches:
        return ObservationLookup(
            LookupStatus.ABSENT,
            missing=missing(f"no provider row for {metric_name} in {game_id}", None, None),
        )
    row = matches[0]
    if row.value is None:
        return ObservationLookup(
            LookupStatus.NULL_VALUE,
            observation=row,
            missing=missing(
                f"provider row for {metric_name} has no value", row.season, row.week
            ),
        )
    return ObservationLookup(LookupStatus.PRESENT, observation=row)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class ContextProviderResult:
    provider: str
    contexts: Sequence[EnrichedPlayerContext] = ()
    raw_payloads: Sequence[RawProviderPayload] = ()
    missing_inputs: Sequence[MissingContextInput] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    observations: Sequence[ProviderMetricObservation] = ()

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "provider": self.provider,
                "contexts": [context.to_dict() for context in self.contexts],
                "raw_payloads": [payload.to_dict() for payload in self.raw_payloads],
                "missing_inputs": [missing.to_dict() for missing in self.missing_inputs],
                "metadata": dict(self.metadata),
                "observations": [row.to_dict() for row in self.observations],
            }
        )


@dataclass(frozen=True)
class RegistryContextResult:
    request: ContextRequest
    contexts: Sequence[EnrichedPlayerContext] = ()
    raw_payloads: Sequence[RawProviderPayload] = ()
    missing_inputs: Sequence[MissingContextInput] = ()
    failures: Sequence[ProviderFailure] = ()
    observations: Sequence[ProviderMetricObservation] = ()
    observation_issues: Sequence[ProviderValidationIssue] = ()

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "request": asdict(self.request),
                "contexts": [context.to_dict() for context in self.contexts],
                "raw_payloads": [payload.to_dict() for payload in self.raw_payloads],
                "missing_inputs": [missing.to_dict() for missing in self.missing_inputs],
                "failures": [failure.to_dict() for failure in self.failures],
                "observations": [row.to_dict() for row in self.observations],
                "observation_issues": [issue.to_dict() for issue in self.observation_issues],
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
    def __init__(
        self,
        providers: Iterable[EnrichedContextProvider] = (),
        *,
        now: float | None = None,
        max_age_seconds: float | None = None,
        season_type: str = "REG",
    ) -> None:
        self.providers = tuple(providers)
        # Freshness inputs for observation validation; ``now`` is injected so
        # staleness checks stay deterministic.
        self.now = now
        self.max_age_seconds = max_age_seconds
        self.season_type = season_type
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
        observations: list[ProviderMetricObservation] = []
        observation_issues: list[ProviderValidationIssue] = []
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
            if result.observations:
                report = validate_observations(
                    result.observations,
                    provider=provider.provider_name,
                    season=request.season,
                    week=request.week,
                    season_type=self.season_type,
                    now=self.now,
                    max_age_seconds=self.max_age_seconds,
                )
                observations.extend(report.accepted)
                observation_issues.extend(report.issues)
                missing.extend(report.missing_inputs(request.season, request.week))
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
            observations=tuple(observations),
            observation_issues=tuple(observation_issues),
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
