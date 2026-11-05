// Post-deploy smoke check for the hosted MCP endpoint.
// Run through Docker: make worker-smoke URL=https://<host>/mcp
// Optional: MCP_SMOKE_BEARER_TOKEN is sent as `Authorization: Bearer <token>`
// (needed once /mcp is protected by OAuth). The token is never printed.

const url = process.argv[2] || process.env.WORKER_SMOKE_URL;
const token = process.env.MCP_SMOKE_BEARER_TOKEN || "";
const attempts = Number(process.env.WORKER_SMOKE_ATTEMPTS || 5);
const delayMs = Number(process.env.WORKER_SMOKE_DELAY_MS || 5000);

if (!url) {
  console.error("worker-smoke: URL is required (make worker-smoke URL=https://<host>/mcp)");
  process.exit(2);
}

function fail(message) {
  throw new Error(message);
}

async function rpc(id, method, params, sessionId) {
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream"
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (sessionId) headers["Mcp-Session-Id"] = sessionId;

  const response = await fetch(url, {
    method: "POST",
    headers,
    redirect: "manual",
    body: JSON.stringify({ jsonrpc: "2.0", id, method, params })
  });
  const body = await response.text();
  if (response.status !== 200) {
    const location = response.headers.get("location");
    fail(`${method}: expected HTTP 200, got ${response.status}${location ? ` (redirect to ${location.split("?")[0]})` : ""}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    fail(`${method}: expected application/json, got "${contentType}"`);
  }
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    fail(`${method}: response is not valid JSON`);
  }
  if (payload.jsonrpc !== "2.0" || payload.id !== id) {
    fail(`${method}: invalid JSON-RPC envelope`);
  }
  if (payload.error) {
    fail(`${method}: JSON-RPC error ${payload.error.code}: ${payload.error.message}`);
  }
  if (!payload.result || typeof payload.result !== "object") {
    fail(`${method}: missing result`);
  }
  return { result: payload.result, sessionId: response.headers.get("mcp-session-id") || sessionId };
}

async function smoke() {
  const init = await rpc(1, "initialize", {
    protocolVersion: "2025-06-18",
    capabilities: {},
    clientInfo: { name: "sleeper-mcp-worker-smoke", version: "0.1.0" }
  });
  if (!init.result.protocolVersion) fail("initialize: missing protocolVersion");
  const list = await rpc(2, "tools/list", {}, init.sessionId);
  const tools = list.result.tools;
  if (!Array.isArray(tools) || tools.length === 0) fail("tools/list: expected a non-empty tools array");
  console.log(`worker-smoke: ok (protocol ${init.result.protocolVersion}, ${tools.length} tools)`);
}

for (let attempt = 1; attempt <= attempts; attempt += 1) {
  try {
    await smoke();
    process.exit(0);
  } catch (error) {
    console.error(`worker-smoke: attempt ${attempt}/${attempts} failed: ${error instanceof Error ? error.message : String(error)}`);
    if (attempt < attempts) await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
}
process.exit(1);
