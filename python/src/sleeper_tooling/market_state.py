from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

ROSTER_AVAILABILITIES = {
    "on_my_roster",
    "rostered",
    "unrostered",
    "reserve",
    "taxi",
    "unknown",
}
MARKET_TYPES = {"free_agent", "waiver", "locked", "unknown"}
MARKET_CONFIDENCES = {"high", "medium", "low"}
ACQUISITION_ACTIONS = {
    "add_now",
    "submit_waiver_claim",
    "unavailable",
    "verify_in_sleeper",
    "watch",
}

DEFAULT_MARKET_TIMEZONE = "America/Los_Angeles"
DEFAULT_WAIVER_PROCESSING_MINUTE = 5
DAILY_WAIVER_RULES = {"free_agent", "waiver", "locked", "waivers_to_fa"}
DAY_NAME_TO_SLEEPER_INDEX = {
    "sun": 0,
    "sunday": 0,
    "mon": 1,
    "monday": 1,
    "tue": 2,
    "tues": 2,
    "tuesday": 2,
    "wed": 3,
    "wednesday": 3,
    "thu": 4,
    "thur": 4,
    "thurs": 4,
    "thursday": 4,
    "fri": 5,
    "friday": 5,
    "sat": 6,
    "saturday": 6,
}
DAILY_WAIVER_INT_RULES = {
    0: "free_agent",
    1: "waiver",
    2: "locked",
    3: "waivers_to_fa",
}


@dataclass(frozen=True)
class MarketState:
    roster_availability: str
    market_type: str
    market_confidence: str
    acquisition_action: str
    market_reason: str
    market_sources: list[str]
    next_market_change_at: str | None = None
    claim_guidance: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        if row["next_market_change_at"] is None:
            row.pop("next_market_change_at")
        if row["claim_guidance"] is None:
            row.pop("claim_guidance")
        return row


@dataclass(frozen=True)
class RosterPlacement:
    availability: str
    roster_id: str | None = None


class MarketStateResolver:
    """Infer current acquisition state from bounded league context."""

    def __init__(
        self,
        *,
        league: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        rosters: list[dict[str, Any]] | None = None,
        my_roster_id: int | str | None = None,
        now: datetime | None = None,
        market_timezone: str = DEFAULT_MARKET_TIMEZONE,
    ) -> None:
        self.league = league or {}
        self.settings = dict(settings or self.league.get("settings") or {})
        self.rosters = rosters or []
        self.my_roster_id = str(my_roster_id) if my_roster_id is not None else None
        self.market_tz = ZoneInfo(market_timezone)
        self.now = ensure_aware(now or datetime.now(timezone.utc)).astimezone(self.market_tz)

    def resolve(
        self,
        player_id: str,
        *,
        player: dict[str, Any] | None = None,
        game: dict[str, Any] | None = None,
        recent_drops: list[dict[str, Any]] | None = None,
    ) -> MarketState:
        player_id = str(player_id)
        player = player or {}
        game = game or {}
        recent_drops = recent_drops or []

        placement = self.roster_placement(player_id)
        if placement.availability != "unrostered":
            reason = "player is already rostered"
            if placement.availability == "on_my_roster":
                reason = "player is already on my roster"
            elif placement.availability in {"reserve", "taxi"}:
                reason = f"player is already rostered in a {placement.availability} slot"
            return self._state(
                roster_availability=placement.availability,
                market_type="locked",
                market_confidence="high",
                acquisition_action="unavailable",
                market_reason=reason,
                market_sources=["league_rosters"],
            )

        drop = self.active_recent_drop(player_id, recent_drops)
        if drop is not None:
            clear_at = drop["clear_at"]
            return self._state(
                roster_availability=placement.availability,
                market_type="waiver",
                market_confidence="high",
                acquisition_action="submit_waiver_claim",
                market_reason="player was recently dropped and has not cleared the league waiver window",
                market_sources=["recent_transactions", "league_settings"],
                next_market_change_at=clear_at,
            )

        daily_rule = self.daily_waiver_rule()
        if daily_rule == "locked":
            return self._state(
                roster_availability=placement.availability,
                market_type="locked",
                market_confidence="high",
                acquisition_action="unavailable",
                market_reason="custom daily waivers lock all acquisitions today",
                market_sources=["league_settings.daily_waivers_days"],
            )
        if daily_rule == "waiver":
            return self._state(
                roster_availability=placement.availability,
                market_type="waiver",
                market_confidence="high",
                acquisition_action="submit_waiver_claim",
                market_reason="custom daily waivers keep players on waivers today",
                market_sources=["league_settings.daily_waivers_days"],
                next_market_change_at=self.next_daily_processing_at(),
            )
        if daily_rule == "free_agent":
            return self._state(
                roster_availability=placement.availability,
                market_type="free_agent",
                market_confidence="high",
                acquisition_action="add_now",
                market_reason="custom daily waivers mark today as free agency",
                market_sources=["league_settings.daily_waivers_days"],
            )
        if daily_rule == "waivers_to_fa":
            process_at = self.today_processing_at()
            if self.now >= process_at:
                return self._state(
                    roster_availability=placement.availability,
                    market_type="free_agent",
                    market_confidence="high",
                    acquisition_action="add_now",
                    market_reason="custom daily waivers processed and players are free agents for the rest of today",
                    market_sources=["league_settings.daily_waivers_days"],
                )
            return self._state(
                roster_availability=placement.availability,
                market_type="waiver",
                market_confidence="high",
                acquisition_action="submit_waiver_claim",
                market_reason="custom daily waivers process later today before switching to free agency",
                market_sources=["league_settings.daily_waivers_days"],
                next_market_change_at=process_at,
            )

        if self.after_game_waivers_enabled() and game_has_started(game, self.now):
            return self._state(
                roster_availability=placement.availability,
                market_type="waiver",
                market_confidence="high",
                acquisition_action="submit_waiver_claim",
                market_reason="player's game has started and after-game waivers are enabled",
                market_sources=["team_schedule", "league_settings"],
                next_market_change_at=self.next_weekly_processing_at(),
            )

        if is_bye_week(player, game):
            return self._state(
                roster_availability=placement.availability,
                market_type="free_agent",
                market_confidence="high",
                acquisition_action="add_now",
                market_reason="player is on bye and no daily or drop waiver rule overrides free agency",
                market_sources=["team_schedule", "sleeper_players"],
            )

        game_start_at = game_start_time(game)
        if (game_start_at is None or self.now < game_start_at.astimezone(self.market_tz)) and self.weekly_waivers_have_processed():
            return self._state(
                roster_availability=placement.availability,
                market_type="free_agent",
                market_confidence="medium",
                acquisition_action="add_now",
                market_reason="weekly waivers have processed and the player's game has not started",
                market_sources=["league_settings", "team_schedule"],
            )

        next_change = self.next_weekly_processing_at()
        return self._state(
            roster_availability=placement.availability,
            market_type="unknown",
            market_confidence="low",
            acquisition_action="verify_in_sleeper",
            market_reason="available roster status is known but league waiver timing is not decisive",
            market_sources=["league_rosters", "league_settings", "team_schedule"],
            next_market_change_at=next_change,
        )

    def roster_placement(self, player_id: str) -> RosterPlacement:
        player_id = str(player_id)
        for roster in self.rosters:
            roster_id = str(roster.get("roster_id") or "")
            reserve_ids = player_id_set(roster.get("reserve"))
            taxi_ids = player_id_set(roster.get("taxi"))
            player_ids = player_id_set(roster.get("players"))
            starter_ids = player_id_set(roster.get("starters"))
            active_ids = player_ids | starter_ids

            if player_id in reserve_ids:
                return RosterPlacement("reserve", roster_id)
            if player_id in taxi_ids:
                return RosterPlacement("taxi", roster_id)
            if player_id in active_ids:
                if self.my_roster_id is not None and roster_id == self.my_roster_id:
                    return RosterPlacement("on_my_roster", roster_id)
                return RosterPlacement("rostered", roster_id)
        return RosterPlacement("unrostered")

    def active_recent_drop(
        self,
        player_id: str,
        recent_drops: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        clear_days = int_value(
            first_present(self.settings, "waiver_clear_days", "waiver_days"),
            default=0,
        )
        if clear_days <= 0:
            return None

        latest_drop_at: datetime | None = None
        latest_drop: dict[str, Any] | None = None
        for drop in recent_drops:
            drop_player_id = str(first_present(drop, "player_id", "player") or "")
            if drop_player_id and drop_player_id != player_id:
                continue
            dropped_at = parse_datetime(first_present(drop, "dropped_at", "created", "created_at", "source_timestamp"))
            if dropped_at is None:
                continue
            if latest_drop_at is None or dropped_at > latest_drop_at:
                latest_drop_at = dropped_at
                latest_drop = drop

        if latest_drop_at is None or latest_drop is None:
            return None
        clear_at = latest_drop_at.astimezone(self.market_tz) + timedelta(days=clear_days)
        if self.now >= clear_at:
            return None
        return {**latest_drop, "clear_at": clear_at}

    def daily_waiver_rule(self) -> str | None:
        if int_value(self.settings.get("daily_waivers"), default=0) <= 0:
            return None

        rule_map = self.daily_waiver_rule_map()
        if not rule_map:
            return None
        today_index = sleeper_day_index(self.now)
        return rule_map.get(today_index)

    def daily_waiver_rule_map(self) -> dict[int, str]:
        raw_days = first_present(
            self.settings,
            "daily_waivers_days",
            "daily_waiver_days",
            "custom_daily_waivers",
            "daily_waivers_schedule",
        )
        if raw_days in (None, ""):
            return {}

        if isinstance(raw_days, dict):
            rules: dict[int, str] = {}
            for key, value in raw_days.items():
                day_index = normalize_day_index(key)
                rule = normalize_daily_waiver_rule(value)
                if day_index is not None and rule is not None:
                    rules[day_index] = rule
            return rules

        if isinstance(raw_days, list):
            return {
                index: rule
                for index, value in enumerate(raw_days[:7])
                if (rule := normalize_daily_waiver_rule(value)) is not None
            }

        return parse_packed_daily_waiver_days(raw_days)

    def today_processing_at(self) -> datetime:
        hour = int_value(
            first_present(self.settings, "daily_waivers_hour", "waiver_hour"),
            default=0,
        )
        minute = int_value(
            first_present(self.settings, "daily_waivers_minute", "waiver_minute"),
            default=DEFAULT_WAIVER_PROCESSING_MINUTE,
        )
        return self.now.replace(hour=hour % 24, minute=minute % 60, second=0, microsecond=0)

    def next_daily_processing_at(self) -> datetime:
        process_at = self.today_processing_at()
        if self.now < process_at:
            return process_at
        return process_at + timedelta(days=1)

    def after_game_waivers_enabled(self) -> bool:
        explicit = first_present(
            self.settings,
            "after_game_waivers",
            "waiver_after_game_starts",
            "waiver_after_game",
        )
        if explicit not in (None, ""):
            return bool_value(explicit)
        waiver_day = first_present(self.settings, "waiver_day_of_week", "waiver_day")
        if waiver_day in (None, ""):
            return False
        if isinstance(waiver_day, str) and waiver_day.strip().lower() in {"none", "off", "disabled"}:
            return False
        return int_value(waiver_day, default=0) > 0

    def weekly_waivers_have_processed(self) -> bool:
        process_at = self.current_weekly_processing_at()
        if process_at is None:
            return not self.after_game_waivers_enabled()
        return self.now >= process_at

    def current_weekly_processing_at(self) -> datetime | None:
        waiver_day = normalize_day_index(first_present(self.settings, "waiver_day_of_week", "waiver_day"))
        if waiver_day is None:
            return None
        days_since_sunday = sleeper_day_index(self.now)
        week_start = self.now.date() - timedelta(days=days_since_sunday)
        process_date = week_start + timedelta(days=(waiver_day + 1) % 7)
        return datetime.combine(process_date, self.weekly_processing_time(), tzinfo=self.market_tz)

    def previous_weekly_processing_at(self) -> datetime | None:
        process_at = self.current_weekly_processing_at()
        if process_at is None:
            return None
        if process_at > self.now:
            return process_at - timedelta(days=7)
        return process_at

    def next_weekly_processing_at(self) -> datetime | None:
        process_at = self.current_weekly_processing_at()
        if process_at is None:
            return None
        if process_at <= self.now:
            return process_at + timedelta(days=7)
        return process_at

    def weekly_processing_time(self) -> time:
        hour = int_value(
            first_present(self.settings, "waiver_hour", "weekly_waivers_hour"),
            default=0,
        )
        minute = int_value(
            first_present(self.settings, "waiver_minute", "weekly_waivers_minute"),
            default=DEFAULT_WAIVER_PROCESSING_MINUTE,
        )
        return time(hour=hour % 24, minute=minute % 60)

    def _state(
        self,
        *,
        roster_availability: str,
        market_type: str,
        market_confidence: str,
        acquisition_action: str,
        market_reason: str,
        market_sources: list[str],
        next_market_change_at: datetime | None = None,
    ) -> MarketState:
        next_change = None
        if next_market_change_at is not None:
            next_change = next_market_change_at.astimezone(self.market_tz).isoformat()
        return MarketState(
            roster_availability=roster_availability,
            market_type=market_type,
            market_confidence=market_confidence,
            acquisition_action=acquisition_action,
            market_reason=market_reason,
            market_sources=market_sources,
            next_market_change_at=next_change,
            claim_guidance=self.claim_guidance(market_type),
        )

    def claim_guidance(self, market_type: str) -> dict[str, Any] | None:
        if market_type != "waiver":
            return None
        if league_uses_faab(self.settings):
            budget = int_value(self.settings.get("waiver_budget"), default=100)
            return {
                "type": "faab",
                "budget": budget,
                "message": "league uses FAAB; generate bid guidance only after player value/risk scoring",
            }
        return {
            "type": "waiver_priority",
            "message": "league uses waiver priority; submit a claim without FAAB bid fields",
        }


def resolve_market_state(
    player_id: str,
    *,
    league: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    rosters: list[dict[str, Any]] | None = None,
    my_roster_id: int | str | None = None,
    player: dict[str, Any] | None = None,
    game: dict[str, Any] | None = None,
    recent_drops: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    market_timezone: str = DEFAULT_MARKET_TIMEZONE,
) -> dict[str, Any]:
    resolver = MarketStateResolver(
        league=league,
        settings=settings,
        rosters=rosters,
        my_roster_id=my_roster_id,
        now=now,
        market_timezone=market_timezone,
    )
    return resolver.resolve(
        player_id,
        player=player,
        game=game,
        recent_drops=recent_drops,
    ).as_dict()


def league_uses_faab(settings: dict[str, Any]) -> bool:
    waiver_type = first_present(settings, "waiver_type", "waiver_order")
    if isinstance(waiver_type, str):
        return waiver_type.strip().lower() in {"2", "faab", "budget", "bidding"}
    return int_value(waiver_type, default=-1) == 2


def player_id_set(values: Any) -> set[str]:
    if not values:
        return set()
    return {str(value) for value in values if value not in (None, "")}


def first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return ensure_aware(value)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if raw.isdigit():
            return parse_datetime(int(raw))
        try:
            return ensure_aware(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def bool_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    return bool(value)


def int_value(value: Any, *, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_day_index(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in DAY_NAME_TO_SLEEPER_INDEX:
            return DAY_NAME_TO_SLEEPER_INDEX[raw]
        if not raw.lstrip("-").isdigit():
            return None
        value = int(raw)
    if isinstance(value, (int, float)):
        day = int(value)
        if 0 <= day <= 6:
            return day
        if 1 <= day <= 7:
            return day % 7
    return None


def sleeper_day_index(value: datetime) -> int:
    return (value.weekday() + 1) % 7


def normalize_daily_waiver_rule(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        normalized = (
            value.strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
            .replace(">", "to")
            .replace("__", "_")
        )
        if normalized in {"fa", "free_agent", "free_agents"}:
            return "free_agent"
        if normalized in {"waiver", "waivers"}:
            return "waiver"
        if normalized in {"lock", "locked"}:
            return "locked"
        if normalized in {"waivers_to_fa", "waiver_to_fa", "waivers_fa"}:
            return "waivers_to_fa"
        if normalized.isdigit():
            return DAILY_WAIVER_INT_RULES.get(int(normalized))
        return None
    if isinstance(value, (int, float)):
        return DAILY_WAIVER_INT_RULES.get(int(value))
    return None


def parse_packed_daily_waiver_days(value: Any) -> dict[int, str]:
    raw = str(value).strip()
    if not raw.isdigit():
        return {}
    digits = raw.zfill(7)
    if len(digits) != 7:
        return {}
    rules: dict[int, str] = {}
    for index, digit in enumerate(digits):
        rule = normalize_daily_waiver_rule(digit)
        if rule is None:
            return {}
        rules[index] = rule
    return rules


def game_start_time(game: dict[str, Any]) -> datetime | None:
    return parse_datetime(
        first_present(
            game,
            "game_start_at",
            "start_at",
            "starts_at",
            "start_time",
            "kickoff_at",
            "kickoff",
        )
    )


def game_has_started(game: dict[str, Any], now: datetime) -> bool:
    status = str(first_present(game, "status", "game_status") or "").strip().lower()
    if status in {"in_progress", "started", "active", "halftime", "final", "complete", "completed", "post"}:
        return True
    if status in {"pre", "pre_game", "scheduled", "created", "bye"}:
        return False
    start_at = game_start_time(game)
    if start_at is None:
        return False
    return ensure_aware(now) >= start_at.astimezone(now.tzinfo)


def is_bye_week(player: dict[str, Any], game: dict[str, Any]) -> bool:
    for row in (game, player):
        if bool_value(first_present(row, "is_bye", "bye")):
            return True
        status = str(first_present(row, "status", "game_status") or "").strip().lower()
        if status == "bye":
            return True
    return False
