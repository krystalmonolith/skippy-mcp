# SkippyMCP — Detailed Design: Architecture, Tool Schemas, Dialect Layer, Error Model

**Document:** `20260609A-skippy-mcp-detailed-design.md`
**Date:** 2026-06-09
**Status:** Detailed design — for review. Supersedes nothing; extends
`20260605A-skippy-mcp-initial-design.md`.
**Precondition this session:** target scope (MSO5204) offline — design must be
fully testable without hardware.

---

## 1. Guiding Constraints

- **Clean Architecture** — dependencies point inward; the domain core knows
  nothing of PyVISA or MCP.
- **Strict typing** — type hints on every function; `mypy --strict` clean.
- **Readability over cleverness** — explicit over magic.
- **Offline-developable** — the whole stack must be unit-testable with no
  instrument attached. This forces transport to be an interface (see §3).
- **Actionable errors** — every user-facing failure names the operation, the
  input, the reason, and what to check/do next. No bare stack traces, no
  "X failed".

---

## 2. Layered Architecture

```
+--------------------------------------------------------------+
|  MCP layer        (tool defs, JSON Schema, error→message)    |  outermost
+--------------------------------------------------------------+
|  Driver layer     (Scope: semantic ops → domain types)       |
+--------------------------------------------------------------+
|  Dialect layer    (semantic op → SCPI string, per series)    |
+--------------------------------------------------------------+
|  Transport layer  (write/query/query_binary over VISA)       |
+--------------------------------------------------------------+
|  Core / domain    (enums, dataclasses; pure, no I/O)         |  innermost
+--------------------------------------------------------------+
```

Proposed package layout (Python, monorepo-style single package):

```
skippy-mcp/
  pyproject.toml
  src/skippy_mcp/
    __init__.py
    core/            # domain types, enums, value objects — pure
      models.py
      enums.py
      errors.py
    transport/       # VISA abstraction + implementations
      base.py        # Transport Protocol
      pyvisa_transport.py
      simulated.py   # in-memory fake scope for offline dev/test
    dialect/         # per-series SCPI mapping
      base.py        # Dialect ABC + registry
      mso5000.py
      generic.py     # conservative fallback
    driver/
      scope.py       # Scope: orchestrates dialect + transport
      session.py     # connect/identify/select-dialect/lifecycle
    mcp/
      server.py      # MCP server wiring
      tools.py       # tool definitions + schema + error translation
    config.py        # server config (resource string, timeouts, flags)
  tests/
    ...
```

---

## 3. Transport Layer (the offline-dev enabler)

A narrow Protocol — everything above it depends only on this, never on PyVISA.

```python
from typing import Protocol

class Transport(Protocol):
    def write(self, command: str) -> None: ...
    def query(self, command: str) -> str: ...
    def query_binary(self, command: str) -> bytes: ...   # IEEE-488.2 block
    def close(self) -> None: ...
```

Implementations:

- **`PyVisaTransport`** — wraps a `pyvisa` `Resource` (`pyvisa-py` backend).
  Owns timeouts, read/write termination, and `query_binary_values()` for blocks.
- **`SimulatedTransport`** — an in-memory model of an MSO5000: holds channel
  state, trigger state, a synthetic waveform generator, and a `:SYSTem:ERRor?`
  queue. Answers queries from state; accepts writes by mutating state. This is
  what makes the design buildable and CI-testable with **no scope attached**.
  It also doubles as the substrate for an eventual demo/offline mode.

> Decision driver: because the hardware is unavailable, `SimulatedTransport` is
> a first-class deliverable, not test scaffolding bolted on later.

---

## 4. Dialect Layer

### 4.1 Responsibility

Translate a semantic operation into the exact SCPI string(s) for a given
instrument **series**, and parse series-specific responses. The driver calls
dialect methods; it never embeds SCPI literals.

### 4.2 Interface — ABC with template-backed defaults

Type-safe method per operation (call sites are checked), with default
implementations that format command templates. A new series subclasses and
overrides only what genuinely differs.

```python
from abc import ABC, abstractmethod
from skippy_mcp.core.enums import Coupling, ImageFormat
from skippy_mcp.core.models import IdnInfo

class Dialect(ABC):
    series: str  # e.g. "MSO5000"

    @abstractmethod
    def matches(self, idn: IdnInfo) -> bool:
        """True if this dialect drives the identified instrument."""

    # --- analog channel ---
    def channel_enable(self, ch: int, on: bool) -> str:
        return f":CHANnel{ch}:DISPlay {'ON' if on else 'OFF'}"

    def channel_scale(self, ch: int, volts_per_div: float) -> str:
        return f":CHANnel{ch}:SCALe {volts_per_div:g}"

    def query_channel_scale(self, ch: int) -> str:
        return f":CHANnel{ch}:SCALe?"

    def channel_coupling(self, ch: int, coupling: Coupling) -> str:
        return f":CHANnel{ch}:COUPling {coupling.scpi}"

    # --- digital / logic (MSO only; base raises NotSupported) ---
    def logic_enable(self, d: int, on: bool) -> str:
        raise NotSupportedError(...)        # overridden by MSO dialects

    # --- acquisition ---
    def run(self) -> str:    return ":RUN"
    def stop(self) -> str:   return ":STOP"
    def single(self) -> str: return ":SINGle"

    # --- screenshot / waveform ---
    @abstractmethod
    def screenshot(self, fmt: ImageFormat) -> str: ...   # returns the cmd

    # --- response parsing ---
    def parse_float(self, raw: str) -> float:
        return float(raw)
```

`MSO5000Dialect(Dialect)` fills in `matches` (model starts with `MSO5`/`DS5`),
implements the `:LA:` and `:BUS<n>:` families, and sets the screenshot command
(`:DISPlay:DATA? PNG`).

### 4.3 Selection — registry + `*IDN?`

```python
DIALECTS: list[type[Dialect]] = [MSO5000Dialect, ...]

def select_dialect(idn: IdnInfo) -> Dialect:
    for cls in DIALECTS:
        d = cls()
        if d.matches(idn):
            return d
    raise UnsupportedInstrumentError(idn)   # actionable: lists supported series
```

Unknown models are **refused** with an actionable `UnsupportedInstrumentError`
that lists the supported series (decision, Q2) — no silent fallback, so an
untested instrument never gets driven with guessed commands.

---

## 5. Error Model

### 5.1 Exception hierarchy (core/errors.py)

```python
class SkippyError(Exception):
    """Base. str(self) is an actionable, user-facing message."""

class ConfigError(SkippyError): ...          # bad server config
class ConnectionFailedError(SkippyError): ...# could not open resource
class InstrumentTimeoutError(SkippyError): ...# VISA timeout on write/query
class UnsupportedInstrumentError(SkippyError):...# no dialect matched
class NotSupportedError(SkippyError): ...    # op invalid for this series
class ValidationError(SkippyError): ...      # bad tool input, caught pre-send
class ScpiError(SkippyError): ...            # scope reported via :SYSTem:ERRor?
```

### 5.2 Three lines of defense

1. **Pre-validation** (before any I/O): channel ∈ 1–4, digital ∈ 0–15, V/div and
   offset within model limits, enum membership. Raises `ValidationError` with the
   offending input — cheap, fast, and keeps malformed SCPI off the wire.
2. **Transport faults**: VISA timeouts/IO errors → `InstrumentTimeoutError` /
   `ConnectionFailedError`, carrying the command that was in flight.
3. **Scope-reported errors**: after state-changing operations, query
   `:SYSTem:ERRor?`; a non-`0,"No error"` response → `ScpiError` with the scope's
   own code+text plus the originating operation.

### 5.3 Actionable message contract

Every `SkippyError` message states: **operation** · **input** · **reason** ·
**what to check/do**. Example:

```
configure_channel: query ':CHANnel1:SCALe?' timed out after 5000 ms.
  Input:  channel=1
  Reason: the scope did not respond within the timeout.
  Check:  (1) scope powered and on the LAN; (2) the configured IP matches the
          scope's Utility → IO address; (3) no modal dialog open on the front
          panel blocking remote control.
```

### 5.4 MCP boundary

The MCP layer catches `SkippyError`, returns the message as an MCP tool error
(`isError`), and logs the full exception internally. Unexpected (non-`Skippy`)
exceptions are wrapped in a generic actionable message and logged with a
traceback — they never leak raw to the model/user.

---

## 6. MCP Tool Surface — Schemas

Connection is established from **server config** (resource string / host,
timeouts, flags) at startup — not via a tool — so every tool assumes a live
session. A read-only `get_identity` reports what's connected.

> JSON Schema sketches below (MCP `inputSchema`). Types abbreviated for the doc.

### `get_identity`  → device identity & selected dialect
- **in:** `{}`
- **out:** `{ manufacturer, model, firmware, hardware, dialect }`

### `configure_channel`
```jsonc
{ "channel": int(1..4),            // required
  "enabled": bool,                 // optional
  "scale_v_per_div": number(>0),
  "offset_v": number,
  "coupling": "DC"|"AC"|"GND",
  "bandwidth_limit": "OFF"|"20M",
  "probe_ratio": number(>0) }
```

### `configure_logic`  (MSO only)
```jsonc
{ "channels": [int(0..15), ...],   // required, ≥1
  "enabled": bool,
  "threshold_v": number,
  "label": string }                // optional per-channel label
```

### `configure_trigger`  (discriminated on `mode`)
```jsonc
{ "mode": "edge"|"pulse"|"pattern", // required
  "source": "CH1".."CH4"|"D0".."D15",
  "slope": "rising"|"falling"|"either",
  "level_v": number,
  // pulse: "polarity","width_op","width_s"
  // pattern: "pattern": { "CH1":"H|L|X|R|F", ... } }
```

### `capture`
```jsonc
{ "action": "run"|"stop"|"single" }   // required
```

### `measure`
```jsonc
{ "type": "vpp"|"vrms"|"freq"|"period"|"duty"|"rise"|"fall"|"delay"|"phase",
  "source": "CH1".."CH4",
  "source2": "CH1".."CH4" }          // required only for delay/phase
// out: { "value": number, "unit": string }
```

### `screenshot`
```jsonc
{ "format": "png"|"bmp" }            // default png
// out: MCP image content (base64)
```

### `read_waveform`
```jsonc
{ "source": "CH1".."CH4"|"D0".."D15",
  "mode": "normal"|"raw"|"max",      // default normal
  "max_points": int }                // server downsamples beyond this
// out: { "x_unit","y_unit","x_increment","values":[...] } (or summary if huge)
```

### `decode_bus`  (MSO only)
```jsonc
{ "bus": int(1..2),
  "protocol": "i2c"|"spi"|"uart"|"parallel"|"can"|"lin",
  "config": { ... protocol-specific ... } }
// out: { "frames": [ { "t": number, "data": "...", "label": "..." }, ... ] }
```

### `scpi_raw`  (escape hatch — **gated**)
```jsonc
{ "command": string,                 // required
  "expect_response": bool }          // default: true if command ends with '?'
// out: { "response": string|null }
```
Gated behind `--allow-raw-scpi`, **disabled by default** (decision, Q3). When
disabled the tool is not registered at all, so the model cannot invoke it.

---

## 7. Connection / Session Lifecycle

1. Read config (resource string or host→build `TCPIP0::host::INSTR`; timeouts;
   flags).
2. Open transport. On failure → `ConnectionFailedError` (actionable: IP, power,
   firewall).
3. `*IDN?` → parse `IdnInfo`.
4. `select_dialect(idn)` → bind dialect to a `Scope`.
5. **`*RST` to a known baseline on connect — ON by default**, disabled with
   `--no-reset` (decision, Q4). When disabled, the scope's existing setup is left
   untouched.
6. Serve tools. On transport loss, surface an actionable error and attempt a
   single reconnect before failing the tool call.

### 7.1 I/O model (sync vs async) — runtime-selectable (decision, Q5)

The driver/transport core is **synchronous** (blocking PyVISA), keeping the
setup-then-poll model simple. The MCP server layer chooses how to dispatch tool
calls:

- **sync (default):** call the blocking driver directly.
- **async (`--async`):** run each blocking driver call in a thread executor so
  the MCP event loop is not blocked.

This is a CLI flag, not two code paths in the driver — the driver is unaware of
the choice; only the server's dispatch wrapper differs.

### 7.2 Command-line options (initial)

| Flag | Default | Effect |
|------|---------|--------|
| `--host <ip\|hostname>` / `--resource <visa>` | — | Target instrument address. |
| `--timeout-ms <n>` | 5000 | VISA I/O timeout. |
| `--async` | off (sync) | Dispatch tool calls via a thread executor (Q5). |
| `--no-reset` | reset on | Skip `*RST` on connect; leave setup untouched (Q4). |
| `--allow-raw-scpi` | off | Enable the `scpi_raw` escape hatch (Q3, pending). |

---

## 8. Testing Strategy (no hardware)

- **Unit:** dialect string-generation (golden SCPI per operation); error mapping;
  pre-validation bounds.
- **Integration via `SimulatedTransport`:** drive `Scope` and the MCP tools
  end-to-end against the fake scope — configure → capture → measure → screenshot
  returns coherent synthetic data.
- **Contract:** every MCP tool's input validates against its JSON Schema; every
  raised `SkippyError` matches the actionable-message contract (operation/input/
  reason/check present).
- **Hardware smoke (deferred):** the §9 PoC from `20260605A`, run live once the
  MSO5204 is on the LAN.

---

## 9. Decisions (all resolved)

- **Q1 — Dialect interface style:** ABC-with-template-defaults (§4.2) — type-safe
  call sites, override only series differences.
- **Q2 — Unknown instrument:** refuse with `UnsupportedInstrumentError` listing
  supported series; no silent fallback (§4.3).
- **Q3 — `scpi_raw` default:** disabled; enabled only via `--allow-raw-scpi`, and
  not registered as a tool when disabled (§6).
- **Q4 — Connect behavior:** `*RST` on connect, ON by default, disabled via
  `--no-reset` (§7).
- **Q5 — Sync vs async:** synchronous core; sync/async dispatch selected at
  runtime via `--async` (§7.1).
- **Q6 — Digital/decode in v1:** native MSO5000 `:LA:`/`:BUS:` only; sigrok
  deferred to a later version.
```
