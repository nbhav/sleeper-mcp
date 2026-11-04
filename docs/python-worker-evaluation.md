# Python Worker Evaluation (issue #40)

Status: evaluation recommendation, 2026-11-04. This is not the Phase 2 migration
decision; per issue #40, Phase 1 does not authorize a production cutover or a final
migration decision.

## Recommendation

**Partial / Hybrid. Do not migrate production now. Retain the TypeScript Worker.**

Mapping to the issue's decision vocabulary: today's answer is **Retain TypeScript
Worker** for production, with an optional **Hybrid** Phase 1 spike
(`weekly_performance_backtest` only) under the DevOps guardrails below. This refines
the two 2026-10-07 owner comments on #40 ("hybrid spike, TS stays production"). I agree
with them, with one change in order: do a package-boundary refactor in `python/` first
(Phase 0). Without it, the spike cannot import shared code without also pulling in
`httpx`, `sqlite3`, `nflreadpy`/`polars`, `rich` and `typer`, so a spike would end up
copying logic again. That is the problem the migration is meant to fix.

Why not a full migration:

1. **Sharing is blocked by package structure, not by Pyodide.** `sleeper_tooling/__init__.py`
   eagerly imports `client`, which imports `httpx` and `db`, and `db` imports `sqlite3`.
   Importing any submodule (even `sleeper_tooling.scoring`) therefore loads the whole
   local runtime stack. `python/pyproject.toml` also lists `nflreadpy==0.1.5`, `rich` and
   `typer` as hard dependencies. A path dependency would vendor polars, requests,
   pydantic and more into the Worker bundle. The PoC below confirms both halves under
   `pywrangler dev`. Through the real `__init__`, imports fail on `httpx`. With a stub
   package, seven pure modules (including `decision_reports` and `player_usage_context`)
   import cleanly on Python 3.14.2.
2. **The tool orchestration layer has to be rewritten regardless.** `mcp_tools.py`
   (2,266 lines) is synchronous (17 `with self._client()` blocks over a sync
   `httpx.Client`). Python Workers do outbound I/O asynchronously through `fetch()` (FFI) or
   async-only HTTP clients. Only the pure helpers carry over unchanged.
3. **The runtime is still moving fast.** `workers-py` shipped 1.17.7, 1.17.8 and 1.17.9
   between 2026-10-06 and 2026-10-23, and `workers-runtime-sdk` 1.9.6 shipped on
   2026-11-04. The Python version is selected by compatibility flags and dates
   (`python_workers_314` became the default on 2026-09-08), and
   `python_shared_standard_library` becomes the default on 2026-11-19, after this
   evaluation date. `workers-py` 1.17.9 also refuses to run with uv older than 0.13.4
   (seen in the PoC).
4. **Cold start and CPU cost more than the TS Worker's.** Cloudflare's own benchmark
   reports a 1.027 s mean cold start for a FastAPI+httpx+pydantic Python Worker, with
   memory snapshots. The startup limit is 1 s of CPU. The TS Worker has not been measured
   here, but JS Workers have no interpreter to restore. On the Workers Free plan
   (10 ms CPU per request), Pyodide running the larger decision helpers is a likely
   blocker. See open question Q1.

What the migration would gain: one implementation of the decision logic instead of two.
The usage-context v3 work (#41, #58) shows that rule changes currently have to be made in
both `python/src/sleeper_tooling/player_usage_context*.py` and
`infra/cloudflare-worker/src/index.ts`, and the two drift. That benefit is real, and it is
why Phase 0 is worth doing even if the Worker never moves to Python.

## Current state of Cloudflare Python Workers (checked 2026-11-04)

| Topic | Finding | Source |
|---|---|---|
| Status | Documented as "a first-class Python experience". The overview page no longer carries a beta label. Requires `compatibility_flags = ["python_workers"]`. | [Python Workers overview][cf-py] |
| Runtime | Pyodide (CPython compiled to WebAssembly) inside V8 isolates. The Pyodide/Python version is chosen by the compatibility date. New versions sit behind a flag until their default date. | [How Python Workers work][cf-how] |
| Python version | `python_workers_314` (Python 3.14) is the default for compatibility dates on or after 2026-09-08. The TS Worker's date is `2026-09-02`, so a spike must set a current date. | [Compatibility flags][cf-flags] |
| Other flags | `python_no_global_handlers` (default from 2025-08-14, requires `class Default(WorkerEntrypoint)`); `python_process_pth_files` (default from 2026-05-26, requires `workers-py >= 1.1.3`); `python_shared_standard_library` (default from **2026-11-19**). | [Compatibility flags][cf-flags] |
| Tooling | `pywrangler` from the `workers-py` package: `uvx --from workers-py pywrangler init`, `uv run pywrangler dev`, `uv run pywrangler deploy`, `pywrangler sync`. Needs **both uv and Node** (it drives Wrangler). Latest is `workers-py` 1.17.9 (2026-10-23, requires Python >=3.11), which depends on `workers-runtime-sdk` (latest 1.9.6, 2026-11-04), `pyodide-cli`, `pyodide-py`, `click`, `rich` and `pyjson5`. | [Overview][cf-py]; PyPI JSON API queried from Docker on 2026-11-04 |
| Packages | "Pure and PyEmscripten Python packages on PyPI" plus packages bundled with Pyodide. Declared in `pyproject.toml` `[project].dependencies`, with dev tools in `[dependency-groups]`. Vendored and bundled at deploy. `requirements.txt` is no longer the path. | [Packages][cf-pkgs] |
| HTTP clients | `fetch()` through FFI (`from js import fetch` or `from workers import fetch`). A docs preview mirror and third-party guides state that `httpx` and `aiohttp` work **async only** and that sync clients (`requests`, sync `httpx.Client`) do not. The main packages page does not say this, so treat it as secondary and verify in the spike. | [FFI][cf-ffi]; [packages preview mirror][cf-pkgs-preview] (secondary) |
| Stdlib | Most of the stdlib is available. `threading` and `multiprocessing` import but do not work. `curses`, `dbm`, `fcntl`, `pwd`, `tkinter`, `venv` and others are missing. The filesystem is in-memory, per isolate, and lost when the isolate is destroyed. `sqlite3` is not on the excluded list but is useless for durable cache. | [Stdlib][cf-stdlib] |
| D1 bindings | `await self.env.DB.prepare(sql).bind(...).run()` / `.first()` from a `WorkerEntrypoint`. Same binding shape as JS, so the `api_response_cache` contract is portable. Results come back as JS proxies and need `to_py()` or a dict conversion (see the `to_js(dict_converter=Object.fromEntries)` pattern). | [D1 from Python][cf-d1]; [FFI][cf-ffi] |
| Cold start | At deploy, the entrypoint and its top-level imports run and the WebAssembly memory is snapshotted. Isolates restore the snapshot. Cloudflare's benchmark (FastAPI+httpx+pydantic) gives a 1.027 s mean cold start; without the snapshot it is about 10 s. This is Cloudflare's workload, not ours. | [How Python Workers work][cf-how]; [changelog 2025-12-08][cf-cold]; [blog][cf-blog] |
| Limits | 128 MB memory per isolate; **1 s startup** (error 10021 "Script startup exceeded CPU time limit"); 64 MiB uncompressed bundle; CPU per request **10 ms on Free**, 30 s default and 5 min max on Paid (`limits.cpu_ms`); 50 subrequests on Free and 10,000 on Paid. Network wait does not count toward CPU. | [Limits][cf-limits] |
| Scheduled handlers | Supported: `async def scheduled(self, controller, env, ctx)` on `Default(WorkerEntrypoint)`. Test locally via `/cdn-cgi/handler/scheduled`. The TS Worker has **no** `scheduled` handler today (only `fetch`), so this is a capability note, not a migration need. A Python cron could later run normalized sync into D1. | [Scheduled handler][cf-sched]; [Cron triggers][cf-cron] |
| ASGI | `from workers import asgi; Default = asgi.entrypoint(app)`, used with FastAPI. Not needed; plain `fetch` is enough for a JSON-RPC endpoint. | [FastAPI][cf-fastapi] |

## Dependency audit (`python/pyproject.toml`)

| Dependency | Used by | Worker verdict |
|---|---|---|
| `httpx>=0.27` | `client.py` (sync `httpx.Client`) | **Do not use the current client.** Use native `fetch()` through FFI, or async `httpx.AsyncClient` if the spike shows it works. A new async `SleeperWorkerClient` is required. |
| `sqlite3` (stdlib) | `db.py` (2,383 lines: `ApiResponseCache`, normalized repository) | **Exclude.** The filesystem is ephemeral and per isolate. Use a D1 adapter that preserves `api_response_cache` (`cache_key`, `url`, `response_json`, `expires_at`, `created_at`), `ttlForUrl` and the 900,000-byte cache-skip limit. |
| `nflreadpy==0.1.5` | `nflverse.py` (imported lazily inside functions) | **Exclude.** Its pure wheel pulls in `polars`, `requests` (sync), `platformdirs`, `tqdm`, `pydantic` and `pydantic-settings`, plus filesystem caching. It is not needed by the TS Worker tools. |
| `rich>=13.7` | `output.py` | **Exclude.** Terminal-only. (`workers-py` depends on rich at build time only.) |
| `typer>=0.12` | `cli.py` | **Exclude.** CLI-only. |
| `zoneinfo` (stdlib) | `market_state.py` (`ZoneInfo(market_timezone)`) | **Fails without tzdata (verified in PoC).** The module imports, but `ZoneInfo("America/New_York")` raises `ZoneInfoNotFoundError` under Pyodide. Add the `tzdata` package as a Worker dependency. |
| `decimal` (stdlib) | `player_usage_context_rules.py` | OK. The C implementation is available. |

## How much code could be shared

Line counts are from `python/src/sleeper_tooling/` at `ad0a093`.

| Bucket | Modules | Lines |
|---|---|---|
| **Shareable as-is if packaging allows** (imports only stdlib or each other) | `decision_reports` 3060, `context_providers` 1258, `roster_analysis` 1059, `player_usage_context_rules` 973, `market_state` 635, `player_usage_context` 544, `player_values` 500, `matchup_model` 342, `league_context` 233, `reports` 208, `context_backtest` 124, `scoring` 76, `season` 7 | **~9,000** |
| **Logic shareable, data missing in Worker** (take a `TrendQueryRepository` / normalized reader backed by SQLite) | `trend_queries` 587, `normalized_decision_reads` 623 | ~1,200 (needs D1 normalized tables, which are out of scope) |
| **Must be rewritten for the Worker** | `mcp_tools` 2266 (sync orchestration → async), `mcp_server` 448 (stdio → HTTP JSON-RPC) | ~2,700 |
| **Not shareable** (local runtime) | `db` 2383, `sync` 1144, `cli` 658, `smoke` 549, `nflverse` 344, `client` 255, `output` 60 | ~5,400 |

The TS Worker (`infra/cloudflare-worker/src/index.ts`, 3,596 lines, 163 functions, 17
tools) is a self-contained port. In a Python Worker, most of its decision logic would be
replaced by imports from the ~9,000 shareable lines. What would remain is roughly
800–1,500 lines of Worker-specific Python: HTTP/JSON-RPC envelope, tool schemas, async
Sleeper client, D1 cache, and an async rewrite of the `mcp_tools` orchestration.

Prerequisites for any sharing (Phase 0; these are source changes and are **not** made in
this evaluation):

1. Make `sleeper_tooling/__init__.py` lazy or empty, so importing a pure submodule does
   not import `client` → `httpx`/`db` → `sqlite3`.
2. Move `nflreadpy`, `rich` and `typer` to optional extras (for example `cli`, `nflverse`),
   or split out a dependency-free core distribution (for example
   `sleeper_tooling_core`) that both the local package and the Worker depend on.
3. Separate I/O from logic in `mcp_tools.py`: each tool becomes "fetch inputs" (sync
   locally, async in the Worker) plus "pure compute" (shared).
4. Add an import guard test (in the existing `python/tests`) asserting that the core
   modules import with `httpx`, `sqlite3`, `rich`, `typer` and `nflreadpy` blocked.

## Proof of concept (`infra/python-worker-spike/`, Docker only)

A throwaway probe, not the Phase 1 spike. It has no MCP envelope, no D1 and no deploy.
It answers one question: can the shared modules run under the real Python Workers
runtime? It uses its own `compose.yml` and does not touch the Makefile, the main Compose
file or CI. `python/src/sleeper_tooling` is mounted read-only into `src/`.

```sh
cd infra/python-worker-spike
export COMPOSE_PROJECT_NAME=sleeper-mcp-pyworker
docker compose build
docker compose run --rm --service-ports pyworker \
  sh -c 'uv sync --locked && uv run pywrangler dev --ip 0.0.0.0 --port 8787'
# in another shell (host port 8789):
#   GET http://127.0.0.1:8789/                  -> {"name": "sleeper-mcp", "endpoint": "/mcp"}
#   GET http://127.0.0.1:8789/probe             -> imports via the real package __init__
#   GET http://127.0.0.1:8789/probe?bypass_init=1  -> imports with a stub package (simulates Phase 0)
```

Results (2026-11-04, Docker Desktop on Apple Silicon, linux/arm64, `workers-py` 1.17.9,
`workers-runtime-sdk` 1.9.6, wrangler 4.157.0, `compatibility_date = "2026-11-01"`,
runtime Python **3.14.2**):

- `pywrangler dev` ran inside Docker without host installs. The `workers-py` 1.17.9 run failed
  first with `uv version at least 0.13.4 required, have 0.9.5`. Pinning
  `ghcr.io/astral-sh/uv:0.13.4` fixed it. This shows the version coupling is real.
- The bundle with the whole `sleeper_tooling` directory was 801 KiB (57 modules, 111.75 KiB vendored),
  well under the 64 MiB limit.
- **Through the real `__init__.py`, every probe failed** with
  `ModuleNotFoundError: No module named 'httpx'` (raised from `sleeper_tooling/__init__.py` →
  `client.py`). This confirms the packaging blocker.
- **With a stub package (simulated lazy `__init__`), all 7 probed modules imported**:
  `scoring`, `reports`, `player_usage_context` (and its rules), `market_state`,
  `decision_reports`, `roster_analysis` and `context_providers`. Reported import times were 0–36 ms
  each. Worker timers are coarse, so treat these as indicative only.
- `ZoneInfo("America/New_York")` raised `ZoneInfoNotFoundError` ("On Pyodide you must do
  pyodide.loadPackage('tzdata')"), so `market_state` needs `tzdata` added as a dependency.

The PoC does not show async fetch, D1, JSON-RPC parity, deployability or performance.
Those remain Phase 1 scope.

## Effort estimate

| Phase | Scope | Estimate |
|---|---|---|
| 0 | Package split, lazy `__init__`, extras, import guard test. Improves the local repo even if the Worker stays TS. | 1–2 days |
| 1 | Spike per issue #40: `GET /`, `POST /mcp` envelope, `weekly_performance_backtest`, async fetch client, D1 adapter + hit/miss/expiry/write/oversize tests, fixture parity vs TS, Docker/Make wiring, preview deploy. | 4–6 days |
| 2 | Full parity for 17 tools: async orchestration rewrite, schema/error parity, golden fixtures for every tool, cold-start/latency/CPU comparison, preview soak, cutover plan. | 3–5 weeks |

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| CPU limit (10 ms on Free) exceeded by Pyodide decision helpers | High if on Free plan | Blocker | Confirm the plan (Q1). Measure CPU per tool in preview. Set `limits.cpu_ms` on Paid. |
| Startup over 1 s once shared modules are imported at top level | Medium | Deploy fails (10021) | Keep top-level imports minimal and measure the snapshot. Lazy-import heavy tools. |
| Cold start (~1 s) vs TS for an interactive MCP client | High | Latency regression | Measure both with the same fixture. Accept or reject in Phase 2. |
| Tooling churn (`workers-py` releases weekly; flag defaults change) | High | Broken builds | Pin `workers-py`/`workers-runtime-sdk` in `uv.lock`, pin `compatibility_date`, upgrade on purpose. |
| Sync-vs-async split creates two orchestration paths | Medium | Logic drift moves rather than disappears | Keep orchestration thin. Put all decisions in pure functions. Parity fixtures. |
| JsProxy/`None`/`null` conversion bugs at FFI and D1 boundaries | Medium | Subtle output differences | Convert at one adapter boundary and normalize JSON in the parity test. |
| `zoneinfo`/tzdata missing under Pyodide | **Confirmed** | `market_state` fails at runtime | Add `tzdata` to the Worker deps and cover it with a test. |
| Local dev parity (`pywrangler dev` in Docker) | Low (PoC worked on linux/arm64) | Friction | Single Compose service. Pin the uv image (`workers-py` 1.17.9 refuses uv < 0.13.4). |

Open questions for the owner:

- **Q1.** Which Workers plan is `sleeper-mcp` on? `wrangler.toml` sets no `limits.cpu_ms`,
  so it cannot be inferred. On Free, the recommendation hardens to "don't".
- **Q2.** Is cold-start latency (~1 s) acceptable to the MCP clients in use, or is p95 on
  warm requests the only metric that matters?

Not measured in this evaluation: TS vs Python cold start, warm latency, bundle size and
CPU. Method for Phase 1/2: same fixture and same `tools/call` payload against both Workers on
preview/workers.dev; at least 20 cold samples (forced by redeploys) and 100 warm samples
per implementation; record `wrangler deploy` bundle size and the CPU time from Workers
analytics/`wrangler tail`.

## DevOps requirements if a spike proceeds

All of these are required. None are implemented by this evaluation.

1. **Separate Worker name and D1 database.** For example `name = "sleeper-mcp-python-spike"`
   and a new D1 database `sleeper-mcp-cache-preview` bound as `SLEEPER_CACHE_DB`. No
   `routes`; use the workers.dev URL only. Never bind the production `sleeper-mcp-cache`.
2. **`preview` GitHub environment, deployed only by `workflow_dispatch`.** Use a new,
   separate workflow file (for example `.github/workflows/deploy-python-worker-preview.yml`)
   with `on: workflow_dispatch` only and `environment: preview`. The preview environment
   holds its own `CLOUDFLARE_API_TOKEN` (scoped to the spike Worker and preview D1) and
   `CLOUDFLARE_ACCOUNT_ID`. The existing `deploy-cloudflare.yml` (`workflow_run` after CI,
   `environment: production`) stays unchanged.
3. **pywrangler/uv only inside a Compose service behind `make pyworker-*`.** Add a
   `cloudflare-python-worker` Compose service (Node + a pinned `ghcr.io/astral-sh/uv`
   binary, workdir `/worker`, named volumes for `.venv`, `python_modules`, `node_modules`)
   and Make targets `pyworker-install`, `pyworker-test`, `pyworker-dev`,
   `pyworker-validate` (`pywrangler deploy --dry-run`) and `pyworker-deploy-preview`. No
   host uv, pip, node or wrangler. Use `COMPOSE_PROJECT_NAME=sleeper-mcp-pyworker` for
   isolation.
4. **CI job outside the required checks.** Add a `python-worker-spike` job (for example in a
   separate `python-worker-spike.yml` workflow, path-filtered to the spike directory) that
   runs `make pyworker-test`. Do not add it to branch protection's required checks, and do
   not change the existing `python-unit`, `sleeper-integration` or `worker-typecheck` jobs.
5. **`workers-py` pinned with a lockfile.** Pin exact `workers-py` (currently 1.17.9) and
   `workers-runtime-sdk` (currently 1.9.6) in `[dependency-groups].dev`, commit
   `uv.lock`, use `uv sync --locked` in Docker and CI, and pin `compatibility_date`. Do
   upgrades as deliberate PRs.
6. **Rollback means the TS Worker keeps the custom-domain route.** The route
   `{ pattern = "sleeper-mcp.neilbhavsar.com", custom_domain = true }` stays only in
   `infra/cloudflare-worker/wrangler.toml`. The Python Worker never declares it, so
   rollback needs no action: stop using the preview URL, or delete the spike Worker. Any
   future cutover must move the route in one reversible PR, with the TS deploy workflow
   still runnable.

## Sources

- `cf-py`: <https://developers.cloudflare.com/workers/languages/python/>
- `cf-how`: <https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/>
- `cf-pkgs`: <https://developers.cloudflare.com/workers/languages/python/packages/>
- `cf-pkgs-preview`: <https://3b21a3e4.previews.developers.cloudflare.com/workers/languages/python/packages/index.md> (preview mirror; secondary)
- `cf-stdlib`: <https://developers.cloudflare.com/workers/languages/python/stdlib/>
- `cf-ffi`: <https://developers.cloudflare.com/workers/languages/python/ffi/>
- [cf-d1]: https://developers.cloudflare.com/d1/examples/query-d1-from-python-workers/
- `cf-flags`: <https://developers.cloudflare.com/workers/configuration/compatibility-flags/>
- `cf-limits`: <https://developers.cloudflare.com/workers/platform/limits/>
- `cf-sched`: <https://developers.cloudflare.com/workers/runtime-apis/handlers/scheduled/>
- `cf-cron`: <https://developers.cloudflare.com/workers/configuration/cron-triggers/>
- `cf-fastapi`: <https://developers.cloudflare.com/workers/languages/python/packages/fastapi/>
- `cf-cold`: <https://developers.cloudflare.com/changelog/2025-12-08-python-cold-start-improvements/>
- `cf-blog`: <https://blog.cloudflare.com/python-workers-advancements>
- PyPI JSON API for `workers-py` and `workers-runtime-sdk`, queried from a `python:3.12-slim` container on 2026-11-04.
- Issue #40 body and owner comments (2026-10-05, 2026-10-07).

[cf-py]: https://developers.cloudflare.com/workers/languages/python/
[cf-how]: https://developers.cloudflare.com/workers/languages/python/how-python-workers-work/
[cf-pkgs]: https://developers.cloudflare.com/workers/languages/python/packages/
[cf-pkgs-preview]: https://3b21a3e4.previews.developers.cloudflare.com/workers/languages/python/packages/index.md
[cf-stdlib]: https://developers.cloudflare.com/workers/languages/python/stdlib/
[cf-ffi]: https://developers.cloudflare.com/workers/languages/python/ffi/
[cf-d1]: https://developers.cloudflare.com/d1/examples/query-d1-from-python-workers/
[cf-flags]: https://developers.cloudflare.com/workers/configuration/compatibility-flags/
[cf-limits]: https://developers.cloudflare.com/workers/platform/limits/
[cf-sched]: https://developers.cloudflare.com/workers/runtime-apis/handlers/scheduled/
[cf-cron]: https://developers.cloudflare.com/workers/configuration/cron-triggers/
[cf-fastapi]: https://developers.cloudflare.com/workers/languages/python/packages/fastapi/
[cf-cold]: https://developers.cloudflare.com/changelog/2025-12-08-python-cold-start-improvements/
[cf-blog]: https://blog.cloudflare.com/python-workers-advancements
