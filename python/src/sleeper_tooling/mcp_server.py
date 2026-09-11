from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from sleeper_tooling.mcp_tools import FantasyToolRunner

PROTOCOL_VERSION = "2024-11-05"


TOOLS = [
    {
        "name": "resolve_league_context",
        "description": "Resolve league, owner, and roster IDs from a Sleeper league URL plus team/user name.",
        "inputSchema": {
            "type": "object",
            "required": ["league_ref"],
            "properties": {
                "league_ref": {"type": "string"},
                "user_ref": {"type": "string"},
                "team_name": {"type": "string", "description": "Deprecated alias for user_ref."},
            },
        },
    },
    {
        "name": "decision_data_status",
        "description": "Report normalized decision data freshness without calling Sleeper.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "season": {"type": "integer"},
                "max_age_hours": {"type": "number", "default": 24},
            },
        },
    },
    {
        "name": "sync_decision_data",
        "description": "Trigger normalized decision data sync through the configured sync service.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "force": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "player_stat_trends",
        "description": "Return graph-friendly normalized player stat rows for one stat key over a week range.",
        "inputSchema": {
            "type": "object",
            "required": ["season", "player_id", "stat_key", "start_week"],
            "properties": {
                "season": {"type": "integer"},
                "player_id": {"type": "string"},
                "stat_key": {"type": "string"},
                "start_week": {"type": "integer"},
                "end_week": {"type": "integer"},
                "source": {"type": "string", "enum": ["stats", "projections"], "default": "stats"},
            },
        },
    },
    {
        "name": "position_stat_leaders",
        "description": "Return graph-friendly normalized stat leaders for a position, week, and stat key.",
        "inputSchema": {
            "type": "object",
            "required": ["season", "week", "position", "stat_key"],
            "properties": {
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "position": {"type": "string"},
                "stat_key": {"type": "string"},
                "source": {"type": "string", "enum": ["stats", "projections"], "default": "stats"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "weekly_briefing",
        "description": "League-aware weekly leaders plus waiver signal for the current or requested week.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "source": {"type": "string", "enum": ["stats", "projections"], "default": "projections"},
                "positions": {"type": "string", "default": "QB,RB,WR,TE,K,DEF"},
                "leader_limit": {"type": "integer", "default": 5},
                "trend_limit": {"type": "integer", "default": 10},
                "lookback_hours": {"type": "integer", "default": 24},
            },
        },
    },
    {
        "name": "weekly_performance_backtest",
        "description": "Back-test weekly leaders and deterministic week-over-week movers for a range of weeks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "season": {"type": "integer"},
                "start_week": {"type": "integer"},
                "weeks": {"type": "integer", "default": 2},
                "positions": {"type": "string", "default": "QB,RB,WR,TE,K,DEF"},
                "source": {"type": "string", "enum": ["stats", "projections"], "default": "stats"},
                "limit": {"type": "integer", "default": 5},
                "movement_limit": {"type": "integer", "default": 5},
            },
        },
    },
    {
        "name": "waiver_watch",
        "description": "Find trending unrostered players with projected value under league scoring.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "positions": {"type": "string", "default": "QB,RB,WR,TE,K,DEF"},
                "trend_type": {"type": "string", "enum": ["add", "drop"], "default": "add"},
                "lookback_hours": {"type": "integer", "default": 24},
                "trend_limit": {"type": "integer", "default": 100},
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "my_lineup",
        "description": "Return the current roster's starters and bench with slots, points so far, and league-scored projections.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "roster_id": {"type": "integer"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "positions": {"type": "string", "default": "QB,RB,WR,TE,K,DEF"},
            },
        },
    },
    {
        "name": "lineup_recommendations",
        "description": "Recommend start/sit moves and compare roster players against available waiver/free-agent options.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "roster_id": {"type": "integer"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "positions": {"type": "string", "default": "QB,RB,WR,TE,K,DEF"},
                "trend_limit": {"type": "integer", "default": 100},
                "lookback_hours": {"type": "integer", "default": 24},
                "min_delta": {"type": "number", "default": 1.0},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "waiver_wire_watch",
        "description": "Return a compact actionable waiver shortlist with availability, projection, trends, injuries, and recent actuals.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "positions": {"type": "string", "default": "QB,RB,WR,TE,K,DEF"},
                "lookback_hours": {"type": "integer", "default": 24},
                "trend_limit": {"type": "integer", "default": 100},
                "limit": {"type": "integer", "default": 25},
                "recent_weeks": {"type": "integer", "default": 3},
            },
        },
    },
    {
        "name": "waiver_wire_by_position",
        "description": "Return top waiver and free-agent options grouped by position with status, drop candidate, gain, and FAAB guidance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "roster_id": {"type": "integer"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "positions": {"type": "string", "default": "QB,RB,WR,TE,K,DEF"},
                "lookback_hours": {"type": "integer", "default": 24},
                "trend_limit": {"type": "integer", "default": 100},
                "per_position_limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "trade_opportunities",
        "description": "Show every opposing team with needs, surplus, trade targets, multiple offer angles, and reasoning.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "roster_id": {"type": "integer"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "positions": {"type": "string", "default": "QB,RB,WR,TE"},
                "targets_per_team": {"type": "integer", "default": 5},
                "offers_per_team": {"type": "integer", "default": 3},
            },
        },
    },
    {
        "name": "decision_smoke_report",
        "description": "Run the compact lineup, waiver, and trade smoke workflow and return display-ready Markdown tables or JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "roster_id": {"type": "integer"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "positions": {"type": "string", "default": "QB,RB,WR,TE,K,DEF"},
                "per_position_limit": {"type": "integer", "default": 3},
                "targets_per_team": {"type": "integer", "default": 2},
                "offers_per_team": {"type": "integer", "default": 2},
                "format": {"type": "string", "enum": ["markdown", "json"], "default": "markdown"},
            },
        },
    },
    {
        "name": "free_agent_watch",
        "description": "Rank currently unrostered players by projection under league scoring.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "positions": {"type": "string", "default": "QB,RB,WR,TE,K,DEF"},
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "injury_watch",
        "description": "List injury-relevant players currently rostered in a league.",
        "inputSchema": {
            "type": "object",
            "properties": {"league_id": {"type": "string"}},
        },
    },
    {
        "name": "opponent_watch",
        "description": "Summarize a roster's weekly opponent, projected starters, and injury flags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "roster_id": {"type": "integer"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
            },
        },
    },
    {
        "name": "league_team_watch",
        "description": "Show completed league transactions for a week, grouped into adds and drops.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "league_id": {"type": "string"},
                "week": {"type": "integer"},
            },
        },
    },
    {
        "name": "player_card",
        "description": "Return player metadata and chart-ready actual vs projected weekly points.",
        "inputSchema": {
            "type": "object",
            "required": ["player_id"],
            "properties": {
                "player_id": {"type": "string"},
                "league_id": {"type": "string"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "weeks_back": {"type": "integer", "default": 6},
            },
        },
    },
]


class McpServer:
    def __init__(self, runner: FantasyToolRunner | None = None) -> None:
        self.runner = runner or FantasyToolRunner()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "notifications/initialized":
            return None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "sleeper-fantasy-tools", "version": "0.1.0"},
                }
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self._call_tool(message.get("params") or {})
            else:
                return self._error(message, -32601, f"Unknown method: {method}")
            return {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            return self._error(message, -32000, str(exc))

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        method = getattr(self.runner, str(name), None)
        if method is None or str(name).startswith("_"):
            raise ValueError(f"Unknown tool: {name}")
        result = method(**arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": result
                    if isinstance(result, str)
                    else json.dumps(result, indent=2, sort_keys=True),
                }
            ],
            "isError": False,
        }

    def _error(self, message: dict[str, Any], code: int, error_message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": code, "message": error_message},
        }


def main() -> None:
    server = McpServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = server.handle(json.loads(line))
        if response is None:
            continue
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
