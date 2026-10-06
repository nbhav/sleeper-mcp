# Docs

This folder holds the detailed project docs. Keep the root `README.md` focused on why the project exists, setup, and the shortest useful examples.

- [Tool Breakdown](tool-breakdown.md): runtime shape, CLI commands, MCP tools, scoring, raw response caching, normalized decision data, and output formats.
- [MCP And Agent Usage](mcp-and-agents.md): local MCP registration, default league/team context, tool selection, token discipline, shareable skill, and remote MCP.
- [Player Context Decision Engine Plan](player-context-decision-engine.md): deterministic role, usage, depth chart, matchup, and volatility plan for better start/sit and waiver decisions.
- [Development And Deployment](development.md): Docker-only commands, tests, CI, Cloudflare deploy, and extension guidelines.

Position-specific Codex skills live under `ai/codex/skills/` for QB, RB, WR,
TE, K, and DEF evaluation. Use them with the deterministic MCP outputs when a
decision needs position-aware interpretation of projections, trends, roster
balance, waivers, or trades.
