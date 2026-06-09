# SkippyMCP — HTTP Transport, Config File, and Auth (Design)

**Document:** `20260609D-skippy-mcp-http-transport-design.md`
**Date:** 2026-06-09
**Status:** Design — for review before implementation.
**Breaking change:** removes the stdio transport; next release is **v0.2.0**
(which will also trigger the deferred release pipeline).

---

## 1. Requirements (from Mark)

1. Optional **JSON configuration file**, path given on the CLI: `--config skippy.json`.
2. `--help` documents the config JSON format.
3. The JSON may specify: **(a)** an API key, **(b)** a set of TLS key files, or
   **(c)** neither → plain HTTP.
4. **Remove the stdio MCP transport entirely** (deemed insecure/problematic).
5. **No config file → plain HTTP, no API key.**
6. On startup, **always print the active mode**: API key and/or TLS, or none.
7. On startup, **print an example smoke-test URL**.

General intent: general public use; **do not assume a reverse proxy** (nginx).
Most common case is a lab on a local LAN where plain HTTP is fine; step up to
HTTP + Bearer API key; most secure is HTTPS/TLS with or without an API key.

---

## 2. Transport

The MCP server becomes **HTTP-only**, using the SDK's **Streamable HTTP**
transport: the low-level `Server` is mounted via `StreamableHTTPSessionManager`
into a Starlette ASGI app and served by **uvicorn**. Endpoint path: **`/mcp`**.

- stdio transport removed (`stdio_server` wiring deleted).
- The driver remains synchronous (blocking PyVISA); HTTP handlers dispatch each
  tool call to a worker thread (`anyio.to_thread.run_sync`) so the event loop is
  never blocked. The old `--async` flag is removed (HTTP is always async-dispatched).
- New runtime deps: `uvicorn`, `starlette` (the latter ships with `mcp`).

---

## 3. Configuration

### 3.1 CLI (instrument + bind + behavior)

| Flag | Default | Purpose |
|------|---------|---------|
| `--host` / `--resource` | — | Instrument address (one required). |
| `--bind` | `0.0.0.0` | HTTP bind address (LAN-reachable by default). |
| `--port` | `8080` | HTTP port. |
| `--timeout-ms` | 5000 | VISA I/O timeout. |
| `--no-reset` | reset on | Skip `*RST` on connect. |
| `--allow-raw-scpi` | off | Register the `scpi_raw` tool. |
| `--config <path>` | none | Optional JSON config (API key / TLS). |

### 3.2 JSON config file

All keys optional. Absent file, or file with neither key → plain HTTP, no auth.

```json
{
  "host": "192.168.1.50",
  "resource": "TCPIP0::192.168.1.50::5555::SOCKET",
  "api_key": "your-bearer-token",
  "tls": {
    "cert": "/path/to/fullchain.pem",
    "key": "/path/to/privkey.pem"
  }
}
```

**Instrument-address precedence** (highest first): CLI `--resource` → CLI
`--host` → JSON `resource` → JSON `host` → else `ConfigError`. CLI always wins
over the JSON file (D2).

Resulting modes:

| `api_key` | `tls` | Mode |
|-----------|-------|------|
| absent | absent | **HTTP**, no auth |
| set | absent | **HTTP + Bearer API key** |
| absent | set | **HTTPS**, no auth |
| set | set | **HTTPS + Bearer API key** |

Validation (actionable `ConfigError`): `--config` path must exist and be valid
JSON; if `tls` is present, both `cert` and `key` must be present and readable;
unknown top-level keys are rejected.

---

## 4. Authentication

When `api_key` is set, a Starlette middleware requires
`Authorization: Bearer <key>` on every request to `/mcp`. Missing/incorrect →
**401** with an actionable JSON body. Constant-time comparison
(`hmac.compare_digest`). When `api_key` is absent, no auth is enforced.

The API key **value is never logged** — startup prints only "enabled/disabled".

---

## 5. TLS

When `tls` is set, uvicorn serves HTTPS directly from `cert`/`key`
(`ssl_certfile` / `ssl_keyfile`) — **no reverse proxy assumed**. When absent,
plain HTTP. (A proxy may still front plain HTTP if the operator chooses; that's
external to SkippyMCP.)

---

## 6. Startup banner

Always printed (to stderr) once the instrument is identified and the server is
about to listen:

```
SkippyMCP 0.2.0 — serving MSO5204 (MSO5000)
  Endpoint : http://0.0.0.0:8080/mcp
  TLS      : disabled
  API key  : disabled
  Tools    : 9
  Smoke test:
    curl -sS http://<host>:8080/mcp \
      -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0"}}}'
```

- Scheme switches to `https` and the smoke line gains
  `-H 'Authorization: Bearer <your-api-key>'` when those modes are active
  (placeholder text, never the real key).
- `<host>` is shown literally as a hint (the bind may be `0.0.0.0`).

---

## 7. `--help`

Argparse epilog documents the `--config` JSON schema (the §3.2 block) and the
mode table, so `skippy-mcp --help` is self-describing.

---

## 8. Security / hygiene

- Config files hold secrets → add `*.local.json`, `skippy.json`, `config*.json`
  to `.gitignore`, and add an API-key-ish pattern + PEM/`tls`-path patterns to
  the local pre-commit `blacklist.txt`.
- Bind defaults to `0.0.0.0` for LAN use; document that exposing beyond a trusted
  LAN warrants API key + TLS.

---

## 9. Testing (hardware-free)

- **Config parsing**: each mode (none/key/tls/both); invalid JSON; missing TLS
  file → actionable `ConfigError`.
- **Auth middleware**: 401 on missing/wrong bearer; 200 path with correct key;
  no-auth mode allows through.
- **Banner**: correct mode lines + scheme for each combination; key value never
  present in output.
- **HTTP end-to-end**: spin the Starlette app against `SimulatedTransport` with
  an in-process ASGI test client; `initialize` + `tools/call` (e.g. get_identity)
  round-trip; assert the existing tool behavior is unchanged over HTTP.

---

## 10. Open gap-decisions (please confirm)

These were not specified; proposed defaults shown — flag any to change:

- **D1 — Bind/port:** default `--bind 0.0.0.0 --port 8080`. (LAN-reachable.)
- **D2 — Instrument address may be in the JSON** (`host`/`resource`) as a
  lower-priority source; CLI `--host`/`--resource` always take precedence
  (resolved 2026-06-09 per Mark).
- **D3 — App-level TLS via uvicorn** (no proxy assumed), per "don't assume nginx".
- **D4 — Remove `--async`**; HTTP always thread-dispatches blocking calls.
- **D5 — Version bump to v0.2.0** on ship (breaking: stdio removed) → triggers
  the deferred release pipeline.
- **D6 — Endpoint path `/mcp`.**
