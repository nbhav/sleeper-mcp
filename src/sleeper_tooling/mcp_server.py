from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from sleeper_tooling.mcp_tools import FantasyToolRunner

PROTOCOL_VERSION = "2024-11-05"


TOOLS = [
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
        "name": "waiver_watch",
        "description": "Find trending unrostered players with projected value under league scoring.",
        "inputSchema": {
            "type": "object",
            "required": ["league_id"],
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
        "name": "free_agent_watch",
        "description": "Rank currently unrostered players by projection under league scoring.",
        "inputSchema": {
            "type": "object",
            "required": ["league_id"],
            "properties": {
                "league_id": {"type": "string"},
                "season": {"type": "integer"},
                "week": {"type": "integer"},
                "positions": {"type": "string", "default": "RB,WR,TE"},
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "injury_watch",
        "description": "List injury-relevant players currently rostered in a league.",
        "inputSchema": {
            "type": "object",
            "required": ["league_id"],
            "properties": {"league_id": {"type": "string"}},
        },
    },
    {
        "name": "opponent_watch",
        "description": "Summarize a roster's weekly opponent, projected starters, and injury flags.",
        "inputSchema": {
            "type": "object",
            "required": ["league_id", "roster_id"],
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
            "required": ["league_id"],
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
                    "text": json.dumps(result, indent=2, sort_keys=True),
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
