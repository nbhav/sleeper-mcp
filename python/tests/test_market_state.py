from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sleeper_tooling.market_state import MarketStateResolver, resolve_market_state

PACIFIC = ZoneInfo("America/Los_Angeles")


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=PACIFIC)


def base_resolver(
    *,
    now: datetime,
    settings: dict | None = None,
    rosters: list[dict] | None = None,
) -> MarketStateResolver:
    return MarketStateResolver(
        settings={
            "waiver_day_of_week": 2,
            "waiver_clear_days": 2,
            **(settings or {}),
        },
        rosters=rosters or [{"roster_id": 1, "players": ["mine"]}],
        my_roster_id=1,
        now=now,
    )


def test_unrostered_after_weekly_waivers_before_game_is_free_agent() -> None:
    state = base_resolver(now=at(2026, 9, 9, 10)).resolve(
        "target",
        game={"game_start_at": at(2026, 9, 10, 18).isoformat()},
    ).as_dict()

    assert state["roster_availability"] == "unrostered"
    assert state["market_type"] == "free_agent"
    assert state["market_confidence"] == "medium"
    assert state["acquisition_action"] == "add_now"
    assert "claim_guidance" not in state


def test_after_game_lock_uses_waiver_market() -> None:
    state = base_resolver(
        now=at(2026, 9, 13, 14),
        settings={"waiver_type": 0},
    ).resolve(
        "target",
        game={"game_start_at": at(2026, 9, 13, 13).isoformat()},
    ).as_dict()

    assert state["roster_availability"] == "unrostered"
    assert state["market_type"] == "waiver"
    assert state["market_confidence"] == "high"
    assert state["acquisition_action"] == "submit_waiver_claim"
    assert state["claim_guidance"]["type"] == "waiver_priority"
    assert state["next_market_change_at"].startswith("2026-09-16T00:05:00")


def test_bye_week_unrostered_player_is_free_agent_without_overrides() -> None:
    state = base_resolver(now=at(2026, 9, 10, 10)).resolve(
        "target",
        player={"bye": False},
        game={"is_bye": True},
    ).as_dict()

    assert state["market_type"] == "free_agent"
    assert state["acquisition_action"] == "add_now"
    assert "bye" in state["market_reason"]


def test_recent_drop_within_clear_window_is_waiver_until_clear_time() -> None:
    state = base_resolver(now=at(2026, 9, 10, 10)).resolve(
        "target",
        recent_drops=[
            {
                "player_id": "target",
                "dropped_at": at(2026, 9, 9, 12).isoformat(),
                "transaction_id": "drop-1",
            }
        ],
    ).as_dict()

    assert state["market_type"] == "waiver"
    assert state["market_confidence"] == "high"
    assert state["acquisition_action"] == "submit_waiver_claim"
    assert state["next_market_change_at"].startswith("2026-09-11T12:00:00")


def test_recent_drop_outside_clear_window_allows_later_rules() -> None:
    state = base_resolver(now=at(2026, 9, 10, 10)).resolve(
        "target",
        game={"game_start_at": at(2026, 9, 13, 11).isoformat()},
        recent_drops=[
            {
                "player_id": "target",
                "dropped_at": at(2026, 9, 7, 9).isoformat(),
                "transaction_id": "drop-1",
            }
        ],
    ).as_dict()

    assert state["market_type"] == "free_agent"
    assert state["acquisition_action"] == "add_now"


def test_custom_daily_waiver_locked_waiver_and_free_agent_variants() -> None:
    locked = base_resolver(
        now=at(2026, 9, 9, 10),
        settings={"daily_waivers": 1, "daily_waivers_days": {"wed": "locked"}},
    ).resolve("target").as_dict()
    waiver = base_resolver(
        now=at(2026, 9, 9, 10),
        settings={
            "daily_waivers": 1,
            "daily_waivers_hour": 2,
            "daily_waivers_days": {"wed": "waivers"},
        },
    ).resolve("target").as_dict()
    free_agent = base_resolver(
        now=at(2026, 9, 9, 10),
        settings={"daily_waivers": 1, "daily_waivers_days": {"wed": "FA"}},
    ).resolve("target").as_dict()

    assert (locked["market_type"], locked["acquisition_action"]) == (
        "locked",
        "unavailable",
    )
    assert (waiver["market_type"], waiver["acquisition_action"]) == (
        "waiver",
        "submit_waiver_claim",
    )
    assert (free_agent["market_type"], free_agent["acquisition_action"]) == (
        "free_agent",
        "add_now",
    )


def test_custom_daily_waivers_to_fa_changes_after_processing_time() -> None:
    before = base_resolver(
        now=at(2026, 9, 9, 1),
        settings={
            "daily_waivers": 1,
            "daily_waivers_hour": 2,
            "daily_waivers_days": {"wed": "waivers_to_fa"},
        },
    ).resolve("target").as_dict()
    after = base_resolver(
        now=at(2026, 9, 9, 3),
        settings={
            "daily_waivers": 1,
            "daily_waivers_hour": 2,
            "daily_waivers_days": {"wed": "waivers_to_fa"},
        },
    ).resolve("target").as_dict()

    assert before["market_type"] == "waiver"
    assert before["next_market_change_at"].startswith("2026-09-09T02:05:00")
    assert after["market_type"] == "free_agent"
    assert after["acquisition_action"] == "add_now"


def test_faab_guidance_only_appears_for_waiver_market() -> None:
    waiver = resolve_market_state(
        "target",
        settings={
            "daily_waivers": 1,
            "daily_waivers_days": {"wed": "waiver"},
            "waiver_type": 2,
            "waiver_budget": 200,
        },
        rosters=[],
        now=at(2026, 9, 9, 10),
    )
    free_agent = resolve_market_state(
        "target",
        settings={
            "daily_waivers": 1,
            "daily_waivers_days": {"wed": "free_agent"},
            "waiver_type": 2,
        },
        rosters=[],
        now=at(2026, 9, 9, 10),
    )
    unknown = resolve_market_state(
        "target",
        settings={"waiver_day_of_week": 2, "waiver_type": 2},
        rosters=[],
        now=at(2026, 9, 8, 10),
        game={"game_start_at": at(2026, 9, 13, 11).isoformat()},
    )

    assert waiver["claim_guidance"] == {
        "type": "faab",
        "budget": 200,
        "message": "league uses FAAB; generate bid guidance only after player value/risk scoring",
    }
    assert "claim_guidance" not in free_agent
    assert unknown["market_type"] == "unknown"
    assert unknown["acquisition_action"] == "verify_in_sleeper"
    assert "claim_guidance" not in unknown
    assert "faab_bid_pct" not in unknown


def test_rostered_and_my_roster_players_are_unavailable_locked() -> None:
    other = base_resolver(
        now=at(2026, 9, 9, 10),
        rosters=[
            {"roster_id": 1, "players": ["mine"], "reserve": ["stash"]},
            {"roster_id": 2, "players": ["theirs"], "taxi": ["rookie"]},
        ],
    )

    mine = other.resolve("mine").as_dict()
    theirs = other.resolve("theirs").as_dict()
    reserve = other.resolve("stash").as_dict()
    taxi = other.resolve("rookie").as_dict()

    assert (mine["roster_availability"], mine["market_type"], mine["acquisition_action"]) == (
        "on_my_roster",
        "locked",
        "unavailable",
    )
    assert (theirs["roster_availability"], theirs["market_type"], theirs["acquisition_action"]) == (
        "rostered",
        "locked",
        "unavailable",
    )
    assert reserve["roster_availability"] == "reserve"
    assert taxi["roster_availability"] == "taxi"
