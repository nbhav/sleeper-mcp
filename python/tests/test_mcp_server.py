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
    tools_by_name = {tool["name"]: tool for tool in response["result"]["tools"]}

    assert tool_names == {
        "resolve_league_context",
        "weekly_briefing",
        "weekly_performance_backtest",
        "waiver_watch",
        "my_lineup",
        "lineup_recommendations",
        "waiver_wire_watch",
        "waiver_wire_by_position",
        "trade_opportunities",
        "decision_smoke_report",
        "free_agent_watch",
        "injury_watch",
        "opponent_watch",
        "league_team_watch",
        "player_card",
    }
    assert tools_by_name["resolve_league_context"]["inputSchema"]["required"] == ["league_ref"]
    assert "user_ref" in tools_by_name["resolve_league_context"]["inputSchema"]["properties"]
    assert "required" not in tools_by_name["waiver_watch"]["inputSchema"]
    assert "required" not in tools_by_name["my_lineup"]["inputSchema"]
    assert "required" not in tools_by_name["lineup_recommendations"]["inputSchema"]
    assert "required" not in tools_by_name["waiver_wire_by_position"]["inputSchema"]
    assert "required" not in tools_by_name["trade_opportunities"]["inputSchema"]
    assert "required" not in tools_by_name["decision_smoke_report"]["inputSchema"]
    assert "required" not in tools_by_name["free_agent_watch"]["inputSchema"]
    assert "required" not in tools_by_name["injury_watch"]["inputSchema"]
    assert "required" not in tools_by_name["opponent_watch"]["inputSchema"]
    assert "required" not in tools_by_name["league_team_watch"]["inputSchema"]
    assert (
        tools_by_name["waiver_wire_by_position"]["inputSchema"]["properties"][
            "per_position_limit"
        ]["default"]
        == 10
    )
    assert (
        tools_by_name["waiver_wire_watch"]["inputSchema"]["properties"][
            "positions"
        ]["default"]
        == "QB,RB,WR,TE,K,DEF"
    )
    assert (
        tools_by_name["free_agent_watch"]["inputSchema"]["properties"][
            "positions"
        ]["default"]
        == "QB,RB,WR,TE,K,DEF"
    )
    assert (
        tools_by_name["trade_opportunities"]["inputSchema"]["properties"][
            "offers_per_team"
        ]["default"]
        == 3
    )
    assert (
        tools_by_name["decision_smoke_report"]["inputSchema"]["properties"]["format"][
            "default"
        ]
        == "markdown"
    )


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


def test_mcp_tool_call_returns_raw_markdown_text_content() -> None:
    class Runner:
        def decision_smoke_report(self, *, format: str = "markdown"):
            return "| Field | Value |\n| --- | --- |\n| Format | " + format + " |"

    response = McpServer(Runner()).handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "decision_smoke_report",
                "arguments": {"format": "markdown"},
            },
        }
    )

    content = response["result"]["content"][0]
    assert content["type"] == "text"
    assert content["text"].startswith("| Field | Value |")
    assert not content["text"].startswith('"')


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
