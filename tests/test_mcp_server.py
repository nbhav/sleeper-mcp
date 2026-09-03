from __future__ import annotations

import json

from sleeper_tooling.mcp_server import McpServer


def test_mcp_initialize_returns_server_capabilities() -> None:
    response = McpServer().handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert response["id"] == 1
    assert response["result"]["capabilities"] == {"tools": {}}
    assert response["result"]["serverInfo"]["name"] == "sleeper-fantasy-tools"


def test_mcp_tools_list_exposes_curated_decision_tools() -> None:
    response = McpServer().handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    tool_names = {tool["name"] for tool in response["result"]["tools"]}

    assert tool_names == {
        "weekly_briefing",
        "waiver_watch",
        "free_agent_watch",
        "injury_watch",
        "opponent_watch",
        "league_team_watch",
        "player_card",
    }


def test_mcp_tool_call_returns_json_text_content() -> None:
    class Runner:
        def injury_watch(self, *, league_id: str):
            return [{"league_id": league_id, "player": "Hurt RB"}]

    response = McpServer(Runner()).handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "injury_watch",
                "arguments": {"league_id": "league-1"},
            },
        }
    )

    content = response["result"]["content"][0]
    assert content["type"] == "text"
    assert json.loads(content["text"]) == [{"league_id": "league-1", "player": "Hurt RB"}]


def test_mcp_unknown_tool_returns_protocol_error() -> None:
    response = McpServer().handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "unknown", "arguments": {}},
        }
    )

    assert response["error"]["code"] == -32000
    assert "Unknown tool" in response["error"]["message"]
