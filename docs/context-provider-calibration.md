# Context Provider Calibration

The optional context provider contract is intentionally separate from scoring.
`ContextProviderRegistry.fetch_context()` calls each configured provider,
validates its provenance, merges non-overlapping player/week fields, retains
raw payloads separately, and reports provider, week, and player missingness.
Provider failures are isolated so Sleeper-only operation remains usable.

The deterministic harness is `python -m sleeper_tooling.context_backtest` and
is runnable with `make context-backtest`. Its fixture rows are versioned in
`DEFAULT_FIXTURES`; no network calls or credentials are involved.

The current rules are deliberately simple and reproducible:

- A spike flag is `spike_score >= 0.70`.
- A role-stability flag is `role_stability_score >= 0.70`.
- A breakout is a positive role change with actual points at least `1.25x`
  projected points.
- A bad add is an add candidate whose actual points are below projection.
- Projection lag is a positive role change where actual points exceed
  projection, regardless of breakout threshold.

The harness reports counts and rates for spike flags preventing bad adds,
role-stability flags catching breakouts, and projections lagging positive role
changes. Formula changes must update `CalibrationRules`, the fixture tests, and
this document together.
