# SkippyMCP

An [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server for
controlling Rigol oscilloscopes from an AI assistant. SkippyMCP translates MCP
tool calls into [SCPI](https://en.wikipedia.org/wiki/Standard_Commands_for_Programmable_Instruments)
commands over [PyVISA](https://pyvisa.readthedocs.io/), so an assistant can
configure channels and triggers, arm captures, read measurements, grab
screenshots, pull waveform data, and read protocol-decode results.

The name is a nod to SCPI — pronounced *"skippy"* in the test-and-measurement
world.

- **Project name:** SkippyMCP
- **Executable:** `skippy-mcp`
- **Primary target:** Rigol **MSO5204** (MSO5000 series); a per-series dialect
  layer keeps other Rigol DSO/MSO families addable.
- **Status:** validated live against a real MSO5204; serves MCP over HTTP
  (Streamable HTTP) with optional Bearer-key auth and TLS.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Run

The server speaks MCP over **HTTP** (Streamable HTTP) at `/mcp`:

```bash
skippy-mcp --host 192.168.1.50            # plain HTTP on 0.0.0.0:8080
skippy-mcp --resource TCPIP0::scope::5555::SOCKET --port 9000
skippy-mcp --config skippy.json           # API key / TLS / address from JSON
```

| Flag | Default | Effect |
|------|---------|--------|
| `--host` / `--resource` | — | Instrument address (CLI overrides the config file). |
| `--bind` | `0.0.0.0` | HTTP bind address. |
| `--port` | `8080` | HTTP port. |
| `--timeout-ms` | 5000 | VISA I/O timeout. |
| `--no-reset` | reset on | Skip `*RST` on connect; leave the setup untouched. |
| `--allow-raw-scpi` | off | Register the `scpi_raw` escape-hatch tool. |
| `--config <path>` | none | Optional JSON config (below). |

### Config file (`--config`)

All keys optional. No file → plain HTTP, no auth.

```json
{
  "host": "192.168.1.50",
  "resource": "TCPIP0::192.168.1.50::5555::SOCKET",
  "api_key": "your-bearer-token",
  "tls": { "cert": "/path/cert.pem", "key": "/path/key.pem" }
}
```

- `api_key` set → require `Authorization: Bearer <key>` on every request.
- `tls` set → serve HTTPS directly (no reverse proxy needed).
- Address precedence: `--resource` > `--host` > JSON `resource` > JSON `host`.

The startup banner reports the active mode (TLS / API key) and prints an example
smoke-test `curl`.

Ready-to-edit examples for each mode live in
[`examples/json-configuration/`](examples/json-configuration/):

| File | Mode |
|------|------|
| [`http-apikey.json`](examples/json-configuration/http-apikey.json) | HTTP + Bearer API key |
| [`https-tls.json`](examples/json-configuration/https-tls.json) | HTTPS/TLS, no auth |
| [`https-tls-apikey.json`](examples/json-configuration/https-tls-apikey.json) | HTTPS/TLS + Bearer API key |

### Generating a self-signed TLS certificate

For local/LAN testing you can make a self-signed cert and key. Include the
address you'll connect to in the `subjectAltName` so clients can verify it:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout key.pem -out cert.pem \
  -subj "/CN=localhost" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
```

Point `tls.cert` / `tls.key` at the resulting files. Clients that don't already
trust the cert can be told to with `SSL_CERT_FILE=cert.pem`. In Docker, the cert
and key must be **readable by the container's non-root user** (e.g. `chmod 644`).
For production, prefer a CA-issued certificate over a self-signed one.

## Docker

```bash
docker build -t skippy-mcp:latest .
# --network host reaches a link-local / same-LAN instrument; -p publishes the API:
docker run --rm --network host skippy-mcp:latest \
  --resource TCPIP0::<scope-ip>::5555::SOCKET --port 8080
```

The image is pure-Python (`pyvisa-py`) and runs as a non-root user.

## Tools

`get_identity`, `configure_channel`, `configure_logic`, `configure_trigger`,
`capture`, `measure`, `screenshot`, `read_waveform`, `decode_bus`, and
(when `--allow-raw-scpi`) `scpi_raw`.

## Develop / test

```bash
.venv/bin/pytest          # full suite, no hardware (uses the simulator)
.venv/bin/mypy            # strict type-check
.venv/bin/ruff check
```

## Documentation

| Document | Description |
|----------|-------------|
| [Initial design](claude-design/20260605A-skippy-mcp-initial-design.md) | Overview, architecture, tool surface, compatible models, prior art. |
| [Detailed design](claude-design/20260609A-skippy-mcp-detailed-design.md) | Layered architecture, transport interface + simulator, dialect layer, error model, tool schemas. |
| [Implementation plan](claude-design/20260609B-skippy-mcp-implementation-plan.md) | Phased build plan (hardware-free through Phase 6). |
| [Validation summary](claude-design/20260609C-skippy-mcp-validation-summary.md) | v0.1.0 test results + live MSO5204 validation. |
| [HTTP transport design](claude-design/20260609D-skippy-mcp-http-transport-design.md) | HTTP transport, config file, API-key auth, TLS (v0.2.0). |

## License

[MIT](LICENSE) © 2026 Mark Deazley
