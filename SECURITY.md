# Security Policy

## Reporting a Vulnerability

Please report security issues privately through GitHub private vulnerability
reporting: open the repository's **Security** tab and choose
**Report a vulnerability**
(<https://github.com/nbhav/sleeper-mcp/security/advisories/new>).

Do not open a public issue, pull request, or discussion for a suspected
vulnerability. Include the affected component (Python CLI/MCP server under
`python/`, or the Cloudflare Worker under `infra/cloudflare-worker/`), steps to
reproduce, and the impact you expect.

## Supported Versions

Only the latest commit on `main` (and the deployed Worker built from it) is
supported. Fixes are not backported.

## Scope Notes

This project wraps the public Sleeper API. Issues in Sleeper itself should be
reported to Sleeper. Never include real secrets such as Cloudflare API tokens
or `data/sleeper-mcp.env` contents in a report.
