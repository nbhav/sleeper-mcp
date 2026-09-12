from __future__ import annotations

import pytest

from sleeper_tooling.sync import (
    SleeperSyncService,
    SyncError,
    resolve_sync_target,
)


def test_resolve_sync_target_defaults_to_current_and_previous_seasons(monkeypatch) -> None:
    monkeypatch.setenv("SLEEPER_DEFAULT_LEAGUE_ID", "league-2026")

    target = resolve_sync_target(state={"season": "2026", "week": 3})

    assert target.to_dict() == {
        "league_id": "league-2026",
        "seasons": [2025, 2026],
        "weeks": [1, 2, 3],
    }


def test_sync_is_idempotent_against_repository_upsert_keys() -> None:
    client = FakeSyncSleeperClient()
    repository = FakeNormalizedRepository()
    service = SleeperSyncService(
        client=client,
        repository=repository,
        clock=FakeClock(),
    )

    first_result = service.sync(league_id="league-2026")
    second_result = service.sync(league_id="league-2026")

    assert first_result.status == "success"
    assert second_result.status == "success"
    assert first_result.row_counts == {
        "league_users": 2,
        "leagues": 2,
        "matchups": 4,
        "nfl_state": 1,
        "players": 2,
        "projections": 4,
        "rosters": 2,
        "stats": 4,
        "transactions": 4,
    }
    assert second_result.row_counts == {
        "league_users": 0,
        "leagues": 0,
        "matchups": 0,
        "nfl_state": 0,
        "players": 0,
        "projections": 0,
        "rosters": 0,
        "stats": 0,
        "transactions": 0,
    }
    assert len(repository.players) == 2
    assert len(repository.player_week_rows) == 8
    assert [run["status"] for run in repository.sync_runs] == ["success", "success"]


def test_sync_records_partial_failure_without_clearing_previous_rows() -> None:
    client = FakeSyncSleeperClient(fail_transactions_week=2)
    repository = FakeNormalizedRepository()
    service = SleeperSyncService(
        client=client,
        repository=repository,
        clock=FakeClock(),
    )

    with pytest.raises(SyncError) as raised:
        service.sync(league_id="league-2026", seasons=[2026], weeks=[1, 2])

    result = raised.value.result
    assert result.status == "failed"
    assert "transactions unavailable" in str(result.error_text)
    assert result.row_counts == {
        "league_users": 1,
        "leagues": 1,
        "matchups": 2,
        "nfl_state": 1,
        "players": 2,
        "projections": 1,
        "rosters": 1,
        "stats": 1,
        "transactions": 1,
    }
    assert repository.sync_runs[-1]["status"] == "failed"
    assert "transactions unavailable" in str(repository.sync_runs[-1]["error_text"])
    assert ("league-2026", 2026, 1, "matchup-1") in repository.matchups
    assert ("league-2026", 2026, 1, "transaction-1") in repository.transactions


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


class FakeSyncSleeperClient:
    def __init__(self, *, fail_transactions_week: int | None = None) -> None:
        self.fail_transactions_week = fail_transactions_week

    def get_nfl_state(self) -> dict[str, object]:
        return {"season": "2026", "week": 2}

    def get_players(self) -> dict[str, dict[str, object]]:
        return {
            "player-1": {"full_name": "One Runner", "position": "RB"},
            "player-2": {"full_name": "Two Receiver", "position": "WR"},
        }

    def get_league(self, league_id: str) -> dict[str, object]:
        if league_id == "league-2025":
            return {
                "league_id": "league-2025",
                "season": "2025",
                "scoring_settings": {"rec": 0.5},
            }
        return {
            "league_id": "league-2026",
            "season": "2026",
            "previous_league_id": "league-2025",
            "scoring_settings": {"rec": 1},
        }

    def get_league_users(self, league_id: str) -> list[dict[str, object]]:
        return [{"user_id": f"user-{league_id}"}]

    def get_rosters(self, league_id: str) -> list[dict[str, object]]:
        return [{"roster_id": 1, "owner_id": f"user-{league_id}", "reserve": []}]

    def get_matchups(self, league_id: str, week: int) -> list[dict[str, object]]:
        return [{"matchup_id": f"matchup-{week}", "roster_id": 1}]

    def get_transactions(self, league_id: str, week: int) -> list[dict[str, object]]:
        if week == self.fail_transactions_week:
            raise RuntimeError(f"transactions unavailable for week {week}")
        return [{"transaction_id": f"transaction-{week}", "type": "waiver"}]

    def get_stats(self, season: int, *, week: int | None = None) -> list[dict[str, object]]:
        return [{"player_id": "player-1", "stats": {"pts_ppr": season + int(week or 0)}}]

    def get_projections(
        self,
        season: int,
        *,
        week: int | None = None,
    ) -> list[dict[str, object]]:
        return [{"player_id": "player-2", "stats": {"pts_ppr": season + int(week or 0)}}]


class FakeNormalizedRepository:
    def __init__(self) -> None:
        self.sync_runs: list[dict[str, object]] = []
        self.nfl_state: dict[str, object] | None = None
        self.players: set[str] = set()
        self.leagues: set[str] = set()
        self.users: set[tuple[str, str]] = set()
        self.rosters: set[tuple[str, int]] = set()
        self.matchups: set[tuple[str, int, int, str]] = set()
        self.transactions: set[tuple[str, int, int, str]] = set()
        self.player_week_rows: set[tuple[str, int, int, str, str]] = set()

    def start_sync_run(
        self,
        *,
        league_id: str,
        seasons: list[int],
        weeks: list[int],
        started_at: float,
    ) -> int:
        self.sync_runs.append(
            {
                "league_id": league_id,
                "seasons": seasons,
                "weeks": weeks,
                "started_at": started_at,
                "status": "running",
            }
        )
        return len(self.sync_runs)

    def finish_sync_run(
        self,
        run_id: int,
        *,
        status: str,
        row_counts: dict[str, int],
        finished_at: float,
        error_text: str | None = None,
    ) -> None:
        self.sync_runs[run_id - 1].update(
            {
                "status": status,
                "row_counts": row_counts,
                "finished_at": finished_at,
                "error_text": error_text,
            }
        )

    def upsert_nfl_state(self, state: dict[str, object]) -> int:
        inserted = 0 if self.nfl_state == state else 1
        self.nfl_state = state
        return inserted

    def upsert_players(self, players: dict[str, dict[str, object]]) -> int:
        return _add_many(self.players, players.keys())

    def upsert_league(self, league: dict[str, object]) -> int:
        return _add_one(self.leagues, str(league["league_id"]))

    def upsert_league_users(
        self,
        *,
        league_id: str,
        users: list[dict[str, object]],
    ) -> int:
        return _add_many(
            self.users,
            ((league_id, str(user["user_id"])) for user in users),
        )

    def upsert_rosters(
        self,
        *,
        league_id: str,
        rosters: list[dict[str, object]],
    ) -> int:
        return _add_many(
            self.rosters,
            ((league_id, int(roster["roster_id"])) for roster in rosters),
        )

    def upsert_matchups(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        matchups: list[dict[str, object]],
    ) -> int:
        return _add_many(
            self.matchups,
            (
                (league_id, season, week, str(matchup["matchup_id"]))
                for matchup in matchups
            ),
        )

    def upsert_transactions(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        transactions: list[dict[str, object]],
    ) -> int:
        return _add_many(
            self.transactions,
            (
                (league_id, season, week, str(transaction["transaction_id"]))
                for transaction in transactions
            ),
        )

    def upsert_player_week_rows(
        self,
        *,
        league_id: str,
        season: int,
        week: int,
        source: str,
        rows: list[dict[str, object]],
        scoring_settings: dict[str, object],
    ) -> int:
        return _add_many(
            self.player_week_rows,
            (
                (league_id, season, week, source, str(row["player_id"]))
                for row in rows
            ),
        )


def _add_one(values: set[object], value: object) -> int:
    if value in values:
        return 0
    values.add(value)
    return 1


def _add_many(values: set[object], incoming: object) -> int:
    inserted = 0
    for value in incoming:
        inserted += _add_one(values, value)
    return inserted
