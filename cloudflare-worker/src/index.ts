const PROTOCOL_VERSION = "2024-11-05";
const APP_BASE_URL = "https://api.sleeper.app/v1";
const DATA_BASE_URL = "https://api.sleeper.com";
const DEFAULT_POSITIONS = "QB,RB,WR,TE,K,DEF";

type JsonMap = Record<string, unknown>;
type Env = {
  SLEEPER_CACHE_DB?: D1Database;
  SLEEPER_DEFAULT_LEAGUE_ID?: string;
  SLEEPER_DEFAULT_ROSTER_ID?: string;
};

const tools = [
  {
    name: "weekly_briefing",
    description: "League-aware weekly leaders plus waiver signal for the current or requested week.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        season: { type: "integer" },
        week: { type: "integer" },
        source: { type: "string", enum: ["stats", "projections"], default: "projections" },
        positions: { type: "string", default: DEFAULT_POSITIONS },
        leader_limit: { type: "integer", default: 5 },
        trend_limit: { type: "integer", default: 10 },
        lookback_hours: { type: "integer", default: 24 }
      }
    }
  },
  {
    name: "waiver_watch",
    description: "Find trending unrostered players with projected value under league scoring.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        season: { type: "integer" },
        week: { type: "integer" },
        positions: { type: "string", default: DEFAULT_POSITIONS },
        trend_type: { type: "string", enum: ["add", "drop"], default: "add" },
        lookback_hours: { type: "integer", default: 24 },
        trend_limit: { type: "integer", default: 100 },
        limit: { type: "integer", default: 25 }
      }
    }
  },
  {
    name: "free_agent_watch",
    description: "Rank currently unrostered players by projection under league scoring.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        season: { type: "integer" },
        week: { type: "integer" },
        positions: { type: "string", default: "RB,WR,TE" },
        limit: { type: "integer", default: 25 }
      }
    }
  },
  {
    name: "injury_watch",
    description: "List injury-relevant players currently rostered in a league.",
    inputSchema: {
      type: "object",
      properties: { league_id: { type: "string" } }
    }
  },
  {
    name: "opponent_watch",
    description: "Summarize a roster's weekly opponent, projected starters, and injury flags.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        roster_id: { type: "integer" },
        season: { type: "integer" },
        week: { type: "integer" }
      }
    }
  },
  {
    name: "league_team_watch",
    description: "Show completed league transactions for a week, grouped into adds and drops.",
    inputSchema: {
      type: "object",
      properties: {
        league_id: { type: "string" },
        week: { type: "integer" }
      }
    }
  },
  {
    name: "player_card",
    description: "Return player metadata and chart-ready actual vs projected weekly points.",
    inputSchema: {
      type: "object",
      required: ["player_id"],
      properties: {
        player_id: { type: "string" },
        league_id: { type: "string" },
        season: { type: "integer" },
        week: { type: "integer" },
        weeks_back: { type: "integer", default: 6 }
      }
    }
  }
];

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/") {
      return jsonResponse({ name: "sleeper-mcp", endpoint: "/mcp" });
    }
    if (url.pathname !== "/mcp") {
      return jsonResponse({ error: "not found" }, 404);
    }
    if (request.method !== "POST") {
      return jsonResponse({ error: "method not allowed" }, 405);
    }

    const message = (await request.json()) as JsonMap;
    const id = message.id;
    try {
      const result = await handleMcpMessage(message, env);
      if (result === null) {
        return new Response(null, { status: 202 });
      }
      return jsonResponse({ jsonrpc: "2.0", id, result });
    } catch (error) {
      return jsonResponse({
        jsonrpc: "2.0",
        id,
        error: {
          code: -32000,
          message: error instanceof Error ? error.message : String(error)
        }
      });
    }
  }
};

async function handleMcpMessage(message: JsonMap, env: Env): Promise<JsonMap | null> {
  const method = String(message.method || "");
  if (method === "notifications/initialized") {
    return null;
  }
  if (method === "initialize") {
    return {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { tools: {} },
      serverInfo: { name: "sleeper-fantasy-tools", version: "0.1.0" }
    };
  }
  if (method === "tools/list") {
    return { tools };
  }
  if (method === "tools/call") {
    const params = objectValue(message.params);
    const name = String(params.name || "");
    const args = objectValue(params.arguments);
    const result = await callTool(name, args, env);
    return {
      content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
      isError: false
    };
  }
  throw new Error(`Unknown method: ${method}`);
}

async function callTool(name: string, args: JsonMap, env: Env): Promise<unknown> {
  switch (name) {
    case "weekly_briefing":
      return weeklyBriefing(args, env);
    case "waiver_watch":
      return waiverWatch(args, env);
    case "free_agent_watch":
      return freeAgentWatch(args, env);
    case "injury_watch":
      return injuryWatch(args, env);
    case "opponent_watch":
      return opponentWatch(args, env);
    case "league_team_watch":
      return leagueTeamWatch(args, env);
    case "player_card":
      return playerCard(args, env);
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

async function weeklyBriefing(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueId = optionalLeagueId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const source = stringArg(args, "source", "projections");
  const scoring = await leagueScoringSettings(leagueId, env);
  const rows = await fetchRowsForPositions(season, week, positions, source, scoring, env);
  const trends = await getTrending("add", numberArg(args, "lookback_hours", 24), numberArg(args, "trend_limit", 10), env);
  const players = await getPlayers(env);
  return {
    season,
    week,
    league_id: leagueId,
    leader_source: source,
    scoring_source: leagueId || "sleeper_default_points",
    leaders: topPlayersByPosition(rows, numberArg(args, "leader_limit", 5)),
    waiver_signal: buildWaiverWatch(trends, players, rows, [], positions, "add")
  };
}

async function waiverWatch(args: JsonMap, env: Env): Promise<JsonMap[]> {
  const leagueId = requireLeagueId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const positions = parsePositions(stringArg(args, "positions", DEFAULT_POSITIONS));
  const trendType = stringArg(args, "trend_type", "add");
  if (!["add", "drop"].includes(trendType)) {
    throw new Error("trend_type must be 'add' or 'drop'");
  }
  const scoring = await leagueScoringSettings(leagueId, env);
  const rows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const [trends, players, rosters] = await Promise.all([
    getTrending(trendType, numberArg(args, "lookback_hours", 24), numberArg(args, "trend_limit", 100), env),
    getPlayers(env),
    getApp(`/league/${leagueId}/rosters`, env)
  ]);
  return withContext(
    buildWaiverWatch(trends, players, rows, arrayValue(rosters), positions, trendType).slice(0, numberArg(args, "limit", 25)),
    { league_id: leagueId, season, week }
  );
}

async function freeAgentWatch(args: JsonMap, env: Env): Promise<JsonMap[]> {
  const leagueId = requireLeagueId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const positions = parsePositions(stringArg(args, "positions", "RB,WR,TE"));
  const scoring = await leagueScoringSettings(leagueId, env);
  const rows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const [players, rosters] = await Promise.all([
    getPlayers(env),
    getApp(`/league/${leagueId}/rosters`, env)
  ]);
  return withContext(
    buildFreeAgentWatch(rows, arrayValue(rosters), players, positions).slice(0, numberArg(args, "limit", 25)),
    { league_id: leagueId, season, week }
  );
}

async function injuryWatch(args: JsonMap, env: Env): Promise<JsonMap[]> {
  const leagueId = requireLeagueId(args, env);
  const [users, rosters, players] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getPlayers(env)
  ]);
  return withContext(buildInjuryWatch(arrayValue(users), arrayValue(rosters), players), { league_id: leagueId });
}

async function opponentWatch(args: JsonMap, env: Env): Promise<JsonMap> {
  const leagueId = requireLeagueId(args, env);
  const rosterId = requireRosterId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const scoring = await leagueScoringSettings(leagueId, env);
  const positions = parsePositions(DEFAULT_POSITIONS);
  const projectionRows = await fetchRowsForPositions(season, week, positions, "projections", scoring, env);
  const [users, rosters, matchups, players] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getApp(`/league/${leagueId}/matchups/${week}`, env),
    getPlayers(env)
  ]);
  return {
    league_id: leagueId,
    roster_id: rosterId,
    season,
    ...buildOpponentWatch(rosterId, week, arrayValue(users), arrayValue(rosters), arrayValue(matchups), players, projectionRows)
  };
}

async function leagueTeamWatch(args: JsonMap, env: Env): Promise<JsonMap[]> {
  const leagueId = requireLeagueId(args, env);
  const [, week] = await resolveSeasonWeek(args, env);
  const [users, rosters, transactions, players] = await Promise.all([
    getApp(`/league/${leagueId}/users`, env),
    getApp(`/league/${leagueId}/rosters`, env),
    getApp(`/league/${leagueId}/transactions/${week}`, env),
    getPlayers(env)
  ]);
  return withContext(buildLeagueTeamWatch(week, arrayValue(users), arrayValue(rosters), arrayValue(transactions), players), {
    league_id: leagueId,
    week
  });
}

async function playerCard(args: JsonMap, env: Env): Promise<JsonMap> {
  const playerId = String(args.player_id || "");
  if (!playerId) {
    throw new Error("player_id is required");
  }
  const leagueId = optionalLeagueId(args, env);
  const [season, week] = await resolveSeasonWeek(args, env);
  const players = await getPlayers(env);
  const player = objectValue(players[playerId]);
  const position = String(player.position || "RB");
  const scoring = await leagueScoringSettings(leagueId, env);
  const weeksBack = numberArg(args, "weeks_back", 6);
  const weeklyPoints: JsonMap[] = [];
  const startWeek = Math.max(1, week - weeksBack + 1);

  for (let targetWeek = startWeek; targetWeek <= week; targetWeek += 1) {
    const [statsRows, projectionRows] = await Promise.all([
      fetchRowsForPositions(season, targetWeek, [position], "stats", scoring, env),
      fetchRowsForPositions(season, targetWeek, [position], "projections", scoring, env)
    ]);
    const statRow = findPlayerRow(statsRows, playerId);
    const projectionRow = findPlayerRow(projectionRows, playerId);
    weeklyPoints.push({
      week: targetWeek,
      actual_points: numberValue(statRow?.points) || 0,
      projected_points: numberValue(projectionRow?.points) || 0
    });
  }

  return {
    player_id: playerId,
    league_id: leagueId,
    name: playerName(player, playerId),
    team: String(player.team || ""),
    position,
    status: String(player.status || ""),
    injury_status: String(player.injury_status || ""),
    season,
    week,
    scoring_source: leagueId || "sleeper_default_points",
    chart_data: { weekly_points: weeklyPoints },
    evidence: [
      "actual_points and projected_points are calculated with league scoring when league_id is provided",
      "missing stat keys are treated as zero"
    ]
  };
}

async function resolveSeasonWeek(args: JsonMap, env: Env): Promise<[number, number]> {
  const season = numberValue(args.season);
  const week = numberValue(args.week);
  if (season !== undefined && week !== undefined) {
    return [season, week];
  }
  const state = objectValue(await getApp("/state/nfl", env));
  return [season ?? Number(state.season), week ?? Number(state.week)];
}

async function leagueScoringSettings(leagueId: string | undefined, env: Env): Promise<JsonMap | undefined> {
  if (!leagueId) {
    return undefined;
  }
  const league = objectValue(await getApp(`/league/${leagueId}`, env));
  return objectValue(league.scoring_settings);
}

async function fetchRowsForPositions(
  season: number,
  week: number,
  positions: string[],
  source: string,
  scoringSettings: JsonMap | undefined,
  env: Env
): Promise<JsonMap[]> {
  const rows: JsonMap[] = [];
  for (const position of positions) {
    const rawRows = arrayValue(await getData(`/${source}/nfl/${season}/${week}`, env, {
      "season_type": "regular",
      "position[]": position,
      "order_by": "pts_ppr"
    }));
    rows.push(...flattenRows(rawRows, scoringSettings).filter((row) => String(row.position || "").toUpperCase() === position));
  }
  return rows;
}

function flattenRows(rows: JsonMap[], scoringSettings: JsonMap | undefined): JsonMap[] {
  return rows.map((row) => {
    const player = objectValue(row.player);
    const stats = objectValue(row.stats);
    const sleeperPoints = stats.pts_ppr || stats.pts_half_ppr || stats.pts_std || row.pts_ppr || row.points || 0;
    if (!scoringSettings) {
      return {
        player_id: String(row.player_id || ""),
        name: String(player.full_name || playerName(player, String(row.player_id || ""))),
        team: String(player.team || ""),
        position: String(player.position || ""),
        points: sleeperPoints
      };
    }
    const [points, breakdown] = calculateFantasyPoints(stats, scoringSettings);
    return {
      player_id: String(row.player_id || ""),
      name: String(player.full_name || playerName(player, String(row.player_id || ""))),
      team: String(player.team || ""),
      position: String(player.position || ""),
      points,
      sleeper_points: sleeperPoints,
      scoring_rules_matched: Object.keys(breakdown).length,
      scoring_breakdown: breakdown
    };
  });
}

function calculateFantasyPoints(stats: JsonMap, scoringSettings: JsonMap): [number, JsonMap] {
  let total = 0;
  const breakdown: JsonMap = {};
  for (const [key, multiplierValue] of Object.entries(scoringSettings)) {
    const stat = numberValue(stats[key]);
    const multiplier = numberValue(multiplierValue);
    if (stat === undefined || multiplier === undefined) {
      continue;
    }
    const points = stat * multiplier;
    if (points === 0) {
      continue;
    }
    breakdown[key] = round(points, 4);
    total += points;
  }
  return [round(total, 2), breakdown];
}

function buildWaiverWatch(
  trends: JsonMap[],
  players: Record<string, JsonMap>,
  projectionRows: JsonMap[],
  rosters: JsonMap[],
  positions: string[],
  trendType: string
): JsonMap[] {
  const rostered = rosteredPlayerIds(rosters);
  const projections = Object.fromEntries(projectionRows.map((row) => [String(row.player_id || ""), row]));
  const allowed = new Set(positions.map((position) => position.toUpperCase()));
  return trends
    .flatMap((trend) => {
      const playerId = String(trend.player_id || "");
      if (!playerId || rostered.has(playerId)) {
        return [];
      }
      const player = objectValue(players[playerId]);
      const projection = objectValue(projections[playerId]);
      const position = String(player.position || projection.position || "");
      if (allowed.size > 0 && !allowed.has(position.toUpperCase())) {
        return [];
      }
      return [{
        player_id: playerId,
        name: playerName(player, playerId),
        team: String(player.team || projection.team || ""),
        position,
        trend_type: trendType,
        trend_count: trend.count || 0,
        projected_points: projection.points || 0,
        sleeper_projected_points: projection.sleeper_points || "",
        status: String(player.status || ""),
        injury_status: String(player.injury_status || "")
      }];
    })
    .sort((a, b) => sortNumber(b.projected_points, a.projected_points) || sortNumber(b.trend_count, a.trend_count));
}

function buildFreeAgentWatch(
  projectionRows: JsonMap[],
  rosters: JsonMap[],
  players: Record<string, JsonMap>,
  positions: string[]
): JsonMap[] {
  const rostered = rosteredPlayerIds(rosters);
  const allowed = new Set(positions.map((position) => position.toUpperCase()));
  return projectionRows
    .flatMap((projection) => {
      const playerId = String(projection.player_id || "");
      if (!playerId || rostered.has(playerId)) {
        return [];
      }
      const player = objectValue(players[playerId]);
      const position = String(player.position || projection.position || "");
      if (allowed.size > 0 && !allowed.has(position.toUpperCase())) {
        return [];
      }
      return [{
        player_id: playerId,
        name: playerName(player, playerId),
        team: String(player.team || projection.team || ""),
        position,
        projected_points: projection.points || 0,
        sleeper_projected_points: projection.sleeper_points || "",
        status: String(player.status || ""),
        injury_status: String(player.injury_status || "")
      }];
    })
    .sort((a, b) => sortNumber(b.projected_points, a.projected_points));
}

function buildInjuryWatch(users: JsonMap[], rosters: JsonMap[], players: Record<string, JsonMap>): JsonMap[] {
  const usersById = Object.fromEntries(users.map((user) => [String(user.user_id || ""), user]));
  const rows: JsonMap[] = [];
  for (const roster of rosters) {
    const owner = objectValue(usersById[String(roster.owner_id || "")]);
    for (const playerIdValue of listValue(roster.players)) {
      const playerId = String(playerIdValue);
      const player = objectValue(players[playerId]);
      if (!isInjuryRelevant(player)) {
        continue;
      }
      rows.push({
        roster_id: roster.roster_id,
        owner_id: roster.owner_id,
        team_name: ownerDisplayName(owner),
        player_id: playerId,
        name: playerName(player, playerId),
        team: String(player.team || ""),
        position: String(player.position || ""),
        status: String(player.status || ""),
        injury_status: String(player.injury_status || "")
      });
    }
  }
  return rows.sort((a, b) => `${a.team_name}${a.position}${a.name}`.localeCompare(`${b.team_name}${b.position}${b.name}`));
}

function buildOpponentWatch(
  rosterId: number,
  week: number,
  users: JsonMap[],
  rosters: JsonMap[],
  matchups: JsonMap[],
  players: Record<string, JsonMap>,
  projectionRows: JsonMap[]
): JsonMap {
  const usersById = Object.fromEntries(users.map((user) => [String(user.user_id || ""), user]));
  const rostersById = Object.fromEntries(rosters.map((roster) => [String(roster.roster_id || ""), roster]));
  const projectionsByPlayer = Object.fromEntries(projectionRows.map((row) => [String(row.player_id || ""), row]));
  const myMatchup = matchups.find((matchup) => Number(matchup.roster_id) === rosterId);
  if (!myMatchup) {
    return { roster_id: rosterId, week, opponent_found: false };
  }
  const matchupId = myMatchup.matchup_id;
  const opponentMatchup = matchups.find((matchup) => matchup.matchup_id === matchupId && Number(matchup.roster_id) !== rosterId);
  if (!opponentMatchup) {
    return { roster_id: rosterId, week, matchup_id: matchupId, opponent_found: false };
  }
  const opponentRosterId = Number(opponentMatchup.roster_id);
  const opponentRoster = objectValue(rostersById[String(opponentRosterId)]);
  const owner = objectValue(usersById[String(opponentRoster.owner_id || "")]);
  const starters = listValue(opponentMatchup.starters).map((playerId) =>
    playerProjectionSummary(String(playerId), players, projectionsByPlayer)
  );
  return {
    roster_id: rosterId,
    week,
    matchup_id: matchupId,
    opponent_found: true,
    opponent_roster_id: opponentRosterId,
    opponent_team_name: ownerDisplayName(owner),
    opponent_points_so_far: opponentMatchup.points || 0,
    opponent_projected_starter_points: round(starters.reduce((sum, row) => sum + (numberValue(row.projected_points) || 0), 0), 2),
    opponent_starters: starters,
    opponent_injuries: starters.filter((row) => row.injury_status || String(row.status || "").toLowerCase() !== "active")
  };
}

function buildLeagueTeamWatch(
  week: number,
  users: JsonMap[],
  rosters: JsonMap[],
  transactions: JsonMap[],
  players: Record<string, JsonMap>
): JsonMap[] {
  const usersById = Object.fromEntries(users.map((user) => [String(user.user_id || ""), user]));
  const rosterOwners = Object.fromEntries(rosters.map((roster) => [String(roster.roster_id || ""), usersById[String(roster.owner_id || "")]]));
  return transactions
    .filter((transaction) => transaction.status === "complete")
    .map((transaction) => {
      const adds = transactionPlayers(objectValue(transaction.adds), players, rosterOwners);
      const drops = transactionPlayers(objectValue(transaction.drops), players, rosterOwners);
      return {
        week,
        transaction_id: transaction.transaction_id,
        type: transaction.type,
        status: transaction.status,
        created: transaction.created,
        roster_ids: transaction.roster_ids || [],
        adds,
        drops,
        adds_summary: adds.map((player) => player.name).join(", "),
        drops_summary: drops.map((player) => player.name).join(", ")
      };
    })
    .sort((a, b) => sortNumber(b.created, a.created));
}

function transactionPlayers(
  playerToRoster: JsonMap,
  players: Record<string, JsonMap>,
  rosterOwners: Record<string, JsonMap>
): JsonMap[] {
  return Object.entries(playerToRoster).map(([playerId, rosterId]) => {
    const player = objectValue(players[playerId]);
    const owner = objectValue(rosterOwners[String(rosterId)]);
    return {
      player_id: playerId,
      name: playerName(player, playerId),
      team: String(player.team || ""),
      position: String(player.position || ""),
      roster_id: rosterId,
      team_name: ownerDisplayName(owner)
    };
  });
}

function topPlayersByPosition(rows: JsonMap[], limit: number): JsonMap[] {
  const grouped: Record<string, JsonMap[]> = {};
  for (const row of rows) {
    const position = String(row.position || "");
    if (!position) {
      continue;
    }
    grouped[position] = [...(grouped[position] || []), row];
  }
  const leaders: JsonMap[] = [];
  for (const [position, positionRows] of Object.entries(grouped)) {
    [...positionRows]
      .sort((a, b) => sortNumber(b.points, a.points))
      .slice(0, limit)
      .forEach((row, index) => leaders.push({ position_rank: index + 1, ...leaderRow(row) }));
  }
  return leaders.sort((a, b) => `${a.position}${a.position_rank}`.localeCompare(`${b.position}${b.position_rank}`));
}

function leaderRow(row: JsonMap): JsonMap {
  const leader: JsonMap = {
    player_id: row.player_id || "",
    name: row.name || "",
    team: row.team || "",
    position: row.position || "",
    points: row.points || 0
  };
  if ("sleeper_points" in row) {
    leader.sleeper_points = row.sleeper_points;
  }
  if ("scoring_rules_matched" in row) {
    leader.scoring_rules_matched = row.scoring_rules_matched;
  }
  return leader;
}

function playerProjectionSummary(playerId: string, players: Record<string, JsonMap>, projectionsByPlayer: Record<string, JsonMap>): JsonMap {
  const player = objectValue(players[playerId]);
  const projection = objectValue(projectionsByPlayer[playerId]);
  return {
    player_id: playerId,
    name: playerName(player, playerId),
    team: String(player.team || projection.team || ""),
    position: String(player.position || projection.position || ""),
    projected_points: projection.points || 0,
    status: String(player.status || ""),
    injury_status: String(player.injury_status || "")
  };
}

function rosteredPlayerIds(rosters: JsonMap[]): Set<string> {
  const playerIds = new Set<string>();
  for (const roster of rosters) {
    for (const playerId of listValue(roster.players)) {
      if (playerId !== null && playerId !== undefined) {
        playerIds.add(String(playerId));
      }
    }
  }
  return playerIds;
}

function findPlayerRow(rows: JsonMap[], playerId: string): JsonMap | undefined {
  return rows.find((row) => String(row.player_id || "") === playerId);
}

async function getPlayers(env: Env): Promise<Record<string, JsonMap>> {
  return recordValue(await getApp("/players/nfl", env));
}

async function getTrending(trendType: string, lookbackHours: number, limit: number, env: Env): Promise<JsonMap[]> {
  return arrayValue(await getApp(`/players/nfl/trending/${trendType}`, env, {
    lookback_hours: String(lookbackHours),
    limit: String(limit)
  }));
}

async function getApp(path: string, env: Env, params: Record<string, string> = {}): Promise<unknown> {
  return getJson(`${APP_BASE_URL}${path}`, params, env);
}

async function getData(path: string, env: Env, params: Record<string, string> = {}): Promise<unknown> {
  return getJson(`${DATA_BASE_URL}${path}`, params, env);
}

async function getJson(rawUrl: string, params: Record<string, string>, env: Env): Promise<unknown> {
  const url = new URL(rawUrl);
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.append(key, value);
  }
  const cacheKey = url.toString();
  const cached = await cacheGet(cacheKey, env);
  if (cached !== undefined) {
    return cached;
  }

  const response = await fetch(url, {
    headers: {
      "Accept": "application/json",
      "User-Agent": "sleeper-mcp-worker/0.1"
    }
  });
  if (!response.ok) {
    throw new Error(`Sleeper API request failed: ${response.status} ${url}`);
  }
  const payload = await response.json();
  await cacheSet(cacheKey, payload, ttlForUrl(cacheKey), env);
  return payload;
}

async function cacheGet(cacheKey: string, env: Env): Promise<unknown | undefined> {
  if (!env.SLEEPER_CACHE_DB) {
    return undefined;
  }
  await ensureCacheTable(env.SLEEPER_CACHE_DB);
  const row = await env.SLEEPER_CACHE_DB.prepare(
    "SELECT response_json FROM api_response_cache WHERE cache_key = ? AND expires_at > ?"
  ).bind(cacheKey, nowSeconds()).first<{ response_json: string }>();
  return row ? JSON.parse(row.response_json) : undefined;
}

async function cacheSet(cacheKey: string, payload: unknown, ttlSeconds: number, env: Env): Promise<void> {
  if (!env.SLEEPER_CACHE_DB) {
    return;
  }
  await ensureCacheTable(env.SLEEPER_CACHE_DB);
  const now = nowSeconds();
  await env.SLEEPER_CACHE_DB.prepare(
    "INSERT OR REPLACE INTO api_response_cache (cache_key, url, response_json, expires_at, created_at) VALUES (?, ?, ?, ?, ?)"
  ).bind(cacheKey, cacheKey, JSON.stringify(payload), now + ttlSeconds, now).run();
}

async function ensureCacheTable(db: D1Database): Promise<void> {
  await db.prepare(
    "CREATE TABLE IF NOT EXISTS api_response_cache (cache_key TEXT PRIMARY KEY, url TEXT NOT NULL, response_json TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL)"
  ).run();
}

function ttlForUrl(url: string): number {
  if (url.includes("/state/nfl")) return 300;
  if (url.includes("/trending/")) return 300;
  if (url.includes("/players/nfl")) return 21600;
  if (url.includes("/stats/") || url.includes("/projections/")) return 900;
  if (url.includes("/matchups/") || url.includes("/transactions/")) return 900;
  return 3600;
}

function optionalLeagueId(args: JsonMap, env: Env): string | undefined {
  const value = String(args.league_id || env.SLEEPER_DEFAULT_LEAGUE_ID || "").trim();
  return value || undefined;
}

function requireLeagueId(args: JsonMap, env: Env): string {
  const value = optionalLeagueId(args, env);
  if (!value) {
    throw new Error("league_id is required; pass league_id or set SLEEPER_DEFAULT_LEAGUE_ID");
  }
  return value;
}

function requireRosterId(args: JsonMap, env: Env): number {
  const value = numberValue(args.roster_id ?? env.SLEEPER_DEFAULT_ROSTER_ID);
  if (value === undefined) {
    throw new Error("roster_id is required; pass roster_id or set SLEEPER_DEFAULT_ROSTER_ID");
  }
  return value;
}

function withContext(rows: JsonMap[], context: JsonMap): JsonMap[] {
  return rows.map((row) => ({ ...context, ...row }));
}

function ownerDisplayName(user: JsonMap): string {
  const metadata = objectValue(user.metadata);
  return String(metadata.team_name || user.display_name || user.username || "Unknown");
}

function playerName(player: JsonMap, playerId: string): string {
  if (typeof player.full_name === "string" && player.full_name) {
    return player.full_name;
  }
  const name = `${String(player.first_name || "")} ${String(player.last_name || "")}`.trim();
  return name || playerId;
}

function isInjuryRelevant(player: JsonMap): boolean {
  if (player.injury_status) {
    return true;
  }
  const status = String(player.status || "");
  return Boolean(status && status.toLowerCase() !== "active");
}

function parsePositions(value: string): string[] {
  return value.split(",").map((position) => position.trim().toUpperCase()).filter(Boolean);
}

function stringArg(args: JsonMap, key: string, fallback: string): string {
  return typeof args[key] === "string" && args[key] ? String(args[key]) : fallback;
}

function numberArg(args: JsonMap, key: string, fallback: number): number {
  return numberValue(args[key]) ?? fallback;
}

function objectValue(value: unknown): JsonMap {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonMap : {};
}

function recordValue(value: unknown): Record<string, JsonMap> {
  const record = objectValue(value);
  return Object.fromEntries(Object.entries(record).map(([key, item]) => [key, objectValue(item)]));
}

function arrayValue(value: unknown): JsonMap[] {
  return Array.isArray(value) ? value.map((item) => objectValue(item)) : [];
}

function listValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function numberValue(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  if (typeof value === "boolean") {
    return value ? 1 : 0;
  }
  return undefined;
}

function sortNumber(left: unknown, right: unknown): number {
  return (numberValue(left) || 0) - (numberValue(right) || 0);
}

function round(value: number, digits: number): number {
  const multiplier = 10 ** digits;
  return Math.round(value * multiplier) / multiplier;
}

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}
