from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60
NORMALIZED_TARGET = "normalized_sleeper_data"


@dataclass(frozen=True)
class NormalizedDecisionInputs:
    league: dict[str, Any]
    users: list[dict[str, Any]]
    rosters: list[dict[str, Any]]
    matchups: list[dict[str, Any]]
    players: dict[str, dict[str, Any]]
    projection_rows: list[dict[str, Any]]
    add_trends: list[dict[str, Any]]
    drop_trends: list[dict[str, Any]]
    recent_actuals: dict[str, list[dict[str, Any]]]


@dataclass(frozen=True)
class NormalizedDecisionRead:
    inputs: NormalizedDecisionInputs | None
    freshness: dict[str, Any]

    @property
    def fresh(self) -> bool:
        return bool(self.inputs is not None and self.freshness.get("status") == "fresh")


class NormalizedDecisionReader:
    def __init__(
        self,
        repository: Any,
        *,
        clock: Callable[[], float] = time.time,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        self.repository = getattr(repository, "repository", repository)
        self.clock = clock
        self.max_age_seconds = max_age_seconds

    def read_inputs(
        self,
        *,
        league_id: str,
        roster_id: int,
        season: int,
        week: int,
        positions: Sequence[str],
        recent_weeks: int = 0,
    ) -> NormalizedDecisionRead:
        missing_methods = self._missing_methods(
            [
                "get_league_settings",
                "list_league_users",
                "list_rosters",
                "list_matchups",
                "list_player_week_rows",
            ]
        )
        if missing_methods:
            return NormalizedDecisionRead(
                None,
                self._freshness(
                    status="unavailable",
                    warnings=[
                        "normalized repository does not expose decision read methods"
                    ],
                    missing_inputs=[f"method:{name}" for name in missing_methods],
                ),
            )

        try:
            league = self._league_payload(
                self.repository.get_league_settings(league_id)
            )
            users = [
                self._user_payload(row)
                for row in self.repository.list_league_users(league_id)
            ]
            rosters = [
                self._roster_payload(row)
                for row in self.repository.list_rosters(league_id)
            ]
            matchups = [
                self._matchup_payload(row)
                for row in self.repository.list_matchups(league_id, season, week)
            ]
            projection_rows = self._player_week_rows(
                season=season,
                week=week,
                source="projections",
                positions=positions,
            )
            recent_actuals = self._recent_actuals(
                season=season,
                week=week,
                positions=positions,
                weeks_back=recent_weeks,
            )
            transactions = self._transactions(league_id=league_id, week=week)
        except Exception as exc:
            return NormalizedDecisionRead(
                None,
                self._freshness(
                    status="unavailable",
                    warnings=[f"normalized decision read failed: {exc}"],
                    missing_inputs=[],
                ),
            )

        require_target_roster = int(roster_id) > 0
        target_roster = next(
            (row for row in rosters if _same_int(row.get("roster_id"), roster_id)),
            None,
        )
        target_matchup = next(
            (row for row in matchups if _same_int(row.get("roster_id"), roster_id)),
            None,
        )
        missing_inputs = []
        if not league:
            missing_inputs.append("league_settings")
        if not users:
            missing_inputs.append("league_users")
        if not rosters:
            missing_inputs.append("rosters")
        if require_target_roster and target_roster is None:
            missing_inputs.append("target_roster")
        if not matchups:
            missing_inputs.append("matchups")
        if require_target_roster and target_matchup is None:
            missing_inputs.append("target_matchup")
        if not projection_rows:
            missing_inputs.append("projections")

        touched_at = self._latest_touched_at(
            [league],
            users,
            rosters,
            matchups,
            projection_rows,
        )
        latest_sync = self._latest_sync(league_id)
        last_synced_at = _number(
            (latest_sync or {}).get("finished_at")
            or (latest_sync or {}).get("started_at")
        )
        reported_synced_at = last_synced_at or touched_at
        stale = self._is_stale(touched_at)

        if missing_inputs:
            return NormalizedDecisionRead(
                None,
                self._freshness(
                    status="missing",
                    last_synced_at=reported_synced_at,
                    latest_sync=latest_sync,
                    warnings=[
                        "normalized decision data is missing required inputs"
                    ],
                    missing_inputs=missing_inputs,
                ),
            )
        if stale:
            return NormalizedDecisionRead(
                None,
                self._freshness(
                    status="stale",
                    last_synced_at=reported_synced_at,
                    latest_sync=latest_sync,
                    warnings=["normalized decision data is stale"],
                    missing_inputs=[],
                ),
            )

        players = self._player_map(
            rosters=rosters,
            matchups=matchups,
            projection_rows=projection_rows,
        )
        add_trends, drop_trends = self._transaction_trends(transactions)
        return NormalizedDecisionRead(
            NormalizedDecisionInputs(
                league=league,
                users=users,
                rosters=rosters,
                matchups=matchups,
                players=players,
                projection_rows=projection_rows,
                add_trends=add_trends,
                drop_trends=drop_trends,
                recent_actuals=recent_actuals,
            ),
            self._freshness(
                status="fresh",
                last_synced_at=reported_synced_at,
                latest_sync=latest_sync,
                warnings=[],
                missing_inputs=[],
            ),
        )

    def _player_week_rows(
        self,
        *,
        season: int,
        week: int,
        source: str,
        positions: Sequence[str],
    ) -> list[dict[str, Any]]:
        allowed = {str(position).upper() for position in positions if str(position)}
        rows = [
            self._player_week_payload(row)
            for row in self.repository.list_player_week_rows(
                season=season,
                week=week,
                source=source,
            )
        ]
        if not allowed:
            return rows
        return [
            row
            for row in rows
            if str(row.get("position") or "").upper() in allowed
        ]

    def _recent_actuals(
        self,
        *,
        season: int,
        week: int,
        positions: Sequence[str],
        weeks_back: int,
    ) -> dict[str, list[dict[str, Any]]]:
        if weeks_back <= 0:
            return {}
        recent_rows: dict[str, list[dict[str, Any]]] = {}
        for target_week in range(max(1, week - weeks_back), week):
            for row in self._player_week_rows(
                season=season,
                week=target_week,
                source="stats",
                positions=positions,
            ):
                player_id = str(row.get("player_id") or "")
                if player_id:
                    recent_rows.setdefault(player_id, []).append(
                        {"week": target_week, "points": row.get("points", 0)}
                    )
        return recent_rows

    def _transactions(self, *, league_id: str, week: int) -> list[dict[str, Any]]:
        if not hasattr(self.repository, "list_transactions"):
            return []
        return [
            self._transaction_payload(row)
            for row in self.repository.list_transactions(league_id, week=week)
        ]

    def _player_map(
        self,
        *,
        rosters: list[dict[str, Any]],
        matchups: list[dict[str, Any]],
        projection_rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        players: dict[str, dict[str, Any]] = {}
        for row in projection_rows:
            player_id = str(row.get("player_id") or "")
            if player_id:
                players[player_id] = self._player_from_projection(row)

        candidate_ids: set[str] = set(players)
        for roster in rosters:
            candidate_ids.update(str(player_id) for player_id in roster.get("players") or [])
            candidate_ids.update(str(player_id) for player_id in roster.get("starters") or [])
            candidate_ids.update(str(player_id) for player_id in roster.get("reserve") or [])
        for matchup in matchups:
            candidate_ids.update(str(player_id) for player_id in matchup.get("players") or [])
            candidate_ids.update(str(player_id) for player_id in matchup.get("starters") or [])

        if hasattr(self.repository, "get_player"):
            for player_id in sorted(candidate_ids):
                row = self.repository.get_player(player_id)
                if row:
                    players[player_id] = {
                        **players.get(player_id, {}),
                        **self._player_payload(row),
                    }
        return players

    def _latest_sync(self, league_id: str) -> dict[str, Any] | None:
        if not hasattr(self.repository, "latest_sync_run"):
            return None
        latest = self.repository.latest_sync_run(
            target=NORMALIZED_TARGET,
            league_id=league_id,
        )
        if latest is None:
            latest = self.repository.latest_sync_run(target=NORMALIZED_TARGET)
        return latest

    def _is_stale(self, last_updated_at: float | None) -> bool:
        if last_updated_at is None:
            return True
        return self.clock() - float(last_updated_at) > self.max_age_seconds

    def _latest_touched_at(self, *groups: Sequence[dict[str, Any]]) -> float | None:
        values = [
            _number(row.get("updated_at"))
            for group in groups
            for row in group
            if row
        ]
        values = [value for value in values if value is not None]
        return max(values) if values else None

    def _freshness(
        self,
        *,
        status: str,
        warnings: list[str],
        missing_inputs: list[str],
        last_synced_at: float | None = None,
        latest_sync: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "fresh": status == "fresh",
            "last_synced_at": last_synced_at,
            "max_age_seconds": self.max_age_seconds,
            "missing_inputs": missing_inputs,
            "warnings": warnings,
            "latest_sync": latest_sync,
        }

    def _missing_methods(self, names: Sequence[str]) -> list[str]:
        return [name for name in names if not hasattr(self.repository, name)]

    def _league_payload(self, row: Mapping[str, Any] | None) -> dict[str, Any]:
        if not row:
            return {}
        raw = dict(row.get("raw") or {})
        raw.update(
            {
                "league_id": row.get("league_id") or raw.get("league_id"),
                "name": row.get("name") or raw.get("name"),
                "season": row.get("season") or raw.get("season"),
                "status": row.get("status") or raw.get("status"),
                "sport": row.get("sport") or raw.get("sport"),
                "scoring_settings": row.get("scoring_settings")
                or raw.get("scoring_settings")
                or {},
                "roster_positions": row.get("roster_positions")
                or raw.get("roster_positions")
                or [],
                "settings": row.get("settings") or raw.get("settings") or {},
                "updated_at": row.get("updated_at"),
            }
        )
        return raw

    def _user_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(row.get("raw") or {})
        raw.update(
            {
                "user_id": row.get("user_id") or raw.get("user_id"),
                "username": row.get("username") or raw.get("username"),
                "display_name": row.get("display_name") or raw.get("display_name"),
                "avatar": row.get("avatar") or raw.get("avatar"),
                "metadata": row.get("metadata") or raw.get("metadata") or {},
                "updated_at": row.get("updated_at"),
            }
        )
        return raw

    def _roster_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(row.get("raw") or {})
        raw.update(
            {
                "roster_id": _int_if_possible(row.get("roster_id")),
                "owner_id": row.get("owner_id") or raw.get("owner_id"),
                "co_owners": row.get("co_owners") or raw.get("co_owners") or [],
                "starters": row.get("starters") or raw.get("starters") or [],
                "players": row.get("players") or raw.get("players") or [],
                "reserve": row.get("reserve") or raw.get("reserve") or [],
                "taxi": row.get("taxi") or raw.get("taxi") or [],
                "settings": row.get("settings") or raw.get("settings") or {},
                "metadata": row.get("metadata") or raw.get("metadata") or {},
                "updated_at": row.get("updated_at"),
            }
        )
        return raw

    def _matchup_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(row.get("raw") or {})
        raw.update(
            {
                "roster_id": _int_if_possible(row.get("roster_id")),
                "matchup_id": row.get("matchup_id") or raw.get("matchup_id"),
                "points": row.get("points") if row.get("points") is not None else raw.get("points", 0),
                "starters": row.get("starters") or raw.get("starters") or [],
                "players": row.get("players") or raw.get("players") or [],
                "players_points": row.get("players_points")
                or raw.get("players_points")
                or {},
                "custom_points": row.get("custom_points")
                or raw.get("custom_points")
                or {},
                "updated_at": row.get("updated_at"),
            }
        )
        return raw

    def _transaction_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(row.get("raw") or {})
        raw.update(
            {
                "transaction_id": row.get("transaction_id")
                or raw.get("transaction_id"),
                "week": row.get("week") or raw.get("week"),
                "type": row.get("type") or raw.get("type"),
                "status": row.get("status") or raw.get("status"),
                "created": row.get("created") or raw.get("created"),
                "roster_ids": row.get("roster_ids") or raw.get("roster_ids") or [],
                "adds": row.get("adds") or raw.get("adds") or {},
                "drops": row.get("drops") or raw.get("drops") or {},
                "updated_at": row.get("updated_at"),
            }
        )
        return raw

    def _player_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        raw = dict(row.get("raw") or {})
        raw.update(
            {
                "player_id": row.get("player_id") or raw.get("player_id"),
                "full_name": row.get("full_name") or raw.get("full_name"),
                "first_name": row.get("first_name") or raw.get("first_name"),
                "last_name": row.get("last_name") or raw.get("last_name"),
                "team": row.get("team") or raw.get("team") or "",
                "position": row.get("position") or raw.get("position") or "",
                "fantasy_positions": row.get("fantasy_positions")
                or raw.get("fantasy_positions")
                or [],
                "status": row.get("status") or raw.get("status") or "",
                "injury_status": row.get("injury_status")
                or raw.get("injury_status")
                or "",
            }
        )
        return raw

    def _player_week_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        player_id = str(row.get("player_id") or "")
        raw = row.get("raw") if isinstance(row.get("raw"), Mapping) else {}
        player = row.get("player") if isinstance(row.get("player"), Mapping) else {}
        name = (
            row.get("player_name")
            or player.get("full_name")
            or raw.get("full_name")
            or player_id
        )
        points = _number(row.get("fantasy_points"))
        if points is None:
            points = _number(row.get("sleeper_points"))
        if points is None:
            stats = raw.get("stats") if isinstance(raw.get("stats"), Mapping) else {}
            points = _first_number(
                stats.get("pts_ppr"),
                stats.get("pts_half_ppr"),
                stats.get("pts_std"),
                raw.get("points"),
            )
        sleeper_points = _number(row.get("sleeper_points"))
        output = {
            "player_id": player_id,
            "name": name,
            "team": row.get("team") or player.get("team") or raw.get("team") or "",
            "position": row.get("position")
            or player.get("position")
            or raw.get("position")
            or "",
            "points": points or 0,
            "sleeper_points": sleeper_points if sleeper_points is not None else "",
            "updated_at": row.get("updated_at"),
            "source_metadata": {"decision_data": "normalized_db"},
        }
        scoring_breakdown = raw.get("scoring_breakdown")
        if isinstance(scoring_breakdown, Mapping):
            output["scoring_breakdown"] = dict(scoring_breakdown)
            output["scoring_rules_matched"] = len(scoring_breakdown)
        return output

    def _player_from_projection(self, row: Mapping[str, Any]) -> dict[str, Any]:
        player_id = str(row.get("player_id") or "")
        return {
            "player_id": player_id,
            "full_name": row.get("name") or player_id,
            "team": row.get("team") or "",
            "position": row.get("position") or "",
            "fantasy_positions": [row.get("position")]
            if row.get("position")
            else [],
            "source_metadata": {"decision_data": "normalized_db"},
        }

    def _transaction_trends(
        self,
        transactions: Sequence[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        add_counts: dict[str, int] = {}
        drop_counts: dict[str, int] = {}
        for transaction in transactions:
            if transaction.get("status") and transaction.get("status") != "complete":
                continue
            for player_id in (transaction.get("adds") or {}).keys():
                add_counts[str(player_id)] = add_counts.get(str(player_id), 0) + 1
            for player_id in (transaction.get("drops") or {}).keys():
                drop_counts[str(player_id)] = drop_counts.get(str(player_id), 0) + 1
        return _trend_rows(add_counts), _trend_rows(drop_counts)


def _trend_rows(counts: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"player_id": player_id, "count": count}
        for player_id, count in sorted(
            counts.items(),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )
    ]


def _same_int(left: Any, right: Any) -> bool:
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _int_if_possible(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _number(value)
        if parsed is not None:
            return parsed
    return None
