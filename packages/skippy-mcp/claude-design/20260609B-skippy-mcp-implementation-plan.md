# SkippyMCP — Implementation Plan

**Document:** `20260609B-skippy-mcp-implementation-plan.md`
**Date:** 2026-06-09
**Status:** PLAN — **awaiting Mark's review. Do not execute until approved.**
**Basis:** `20260605A-skippy-mcp-initial-design.md`,
`20260609A-skippy-mcp-detailed-design.md` (all six design questions resolved).

---

## 0. Principles for this plan

- **Hardware-free first.** Phases 1–6 require no scope; everything is built and
  tested against `SimulatedTransport`. The live MSO5204 is only needed for the
  Phase 7 smoke test.
- **Inside-out.** Build from the domain core outward, so each layer is testable
  the moment it lands.
- **Each phase is independently mergeable** with its own tests green and
  `mypy --strict` clean. Tests run after every phase (per Mark's workflow).
- **Tooling:** Python 3.x, `pyproject.toml`, `mypy --strict`, `ruff`, `pytest`.
  Fresh in-repo `.venv`. Type hints on every function.

---

## Phase 0 — Project scaffolding (no app logic)

**Goal:** an installable, lint/type/test-clean empty package.

Deliverables:
- `pyproject.toml` — project metadata, deps (`pyvisa`, `pyvisa-py`, MCP SDK),
  dev deps (`pytest`, `mypy`, `ruff`), entry point `skippy-mcp = skippy_mcp.mcp.server:main`.
- Fresh `.venv/` in repo; deps installed.
- `src/skippy_mcp/__init__.py` (version), package dirs `core/ transport/ dialect/
  driver/ mcp/` each with `__init__.py`.
- `tests/` with one trivial passing test.
- `README.md` stub, `.gitignore` (`.venv/`, `__pycache__/`, build artifacts).
- `mypy.ini`/`ruff` config (or in `pyproject.toml`).

Acceptance: `pip install -e .` works; `pytest`, `mypy --strict src`, `ruff check`
all pass on the empty skeleton.

---

## Phase 1 — Core domain (`core/`)

**Goal:** pure, I/O-free domain types. No PyVISA, no MCP imports.

Deliverables:
- `core/enums.py` — `Coupling`, `BandwidthLimit`, `TriggerMode`, `TriggerSlope`,
  `MeasurementType`, `BusProtocol`, `ImageFormat`, `CaptureAction`,
  `WaveformMode`. Each enum carries its SCPI token where relevant (e.g.
  `Coupling.DC.scpi == "DC"`).
- `core/models.py` — frozen dataclasses: `IdnInfo`, `ChannelConfig`,
  `LogicConfig`, `TriggerConfig`, `MeasurementResult`, `WaveformData`,
  `Screenshot`, `DecodedFrame`, `DeviceLimits` (per-model bounds).
- `core/errors.py` — `SkippyError` hierarchy (§5.1 of detailed design) with the
  actionable-message contract (operation · input · reason · check).

Tests: enum↔SCPI mapping; dataclass immutability; error messages contain all four
contract elements.

Acceptance: `core` imports nothing from `transport`/`dialect`/`mcp`; full type
coverage; tests green.

---

## Phase 2 — Transport layer (`transport/`)

**Goal:** the VISA abstraction + the offline simulator that unblocks everything.

Deliverables:
- `transport/base.py` — `Transport` Protocol (`write`, `query`, `query_binary`,
  `close`).
- `transport/simulated.py` — `SimulatedTransport`: in-memory MSO5000 model.
  Holds channel/trigger/logic state, a synthetic waveform generator, a
  `:SYSTem:ERRor?` queue, and responds to `*IDN?` as an MSO5204. Accepts the
  SCPI the MSO5000 dialect will emit; unknown commands queue a SCPI error.
- `transport/pyvisa_transport.py` — `PyVisaTransport`: wraps a `pyvisa`
  `Resource`; manages timeout, termination, `query_binary_values()`. Maps VISA
  exceptions → `InstrumentTimeoutError`/`ConnectionFailedError`.

Tests: simulator round-trips `*IDN?`, channel set/query, error-queue behavior,
binary block for a screenshot stub. (`PyVisaTransport` covered by a thin mock;
real I/O deferred to Phase 7.)

Acceptance: simulator is rich enough to back Phases 3–6; transport depends only
on `core`.

---

## Phase 3 — Dialect layer (`dialect/`)

**Goal:** semantic op → SCPI string, per series, type-safe.

Deliverables:
- `dialect/base.py` — `Dialect` ABC (template-backed defaults) + registry +
  `select_dialect(idn)` (refuse unknown → `UnsupportedInstrumentError`, Q2).
- `dialect/mso5000.py` — `MSO5000Dialect`: `matches()` (model `MSO5*`/`DS5*`),
  analog channel, acquisition, trigger (edge/pulse/pattern), measurement items,
  screenshot (`:DISPlay:DATA? PNG`), waveform, and the `:LA:`/`:BUS<n>:` families.
- `dialect/generic.py` — placeholder only (not used; unknown models are refused).

Tests: golden SCPI strings for every operation; `select_dialect` picks MSO5000
for an MSO5204 `*IDN?` and refuses an unknown model with an actionable error.

Acceptance: no SCPI literal exists outside `dialect/`.

---

## Phase 4 — Driver layer (`driver/`) + config + CLI

**Goal:** orchestrate dialect + transport into semantic operations returning
domain types; connection lifecycle; CLI flags.

Deliverables:
- `config.py` — `ServerConfig` from CLI/env: `--host`/`--resource`,
  `--timeout-ms` (5000), `--async`, `--no-reset`, `--allow-raw-scpi` (§7.2).
- `driver/session.py` — connect → `*IDN?` → `select_dialect` → optional `*RST`
  (ON unless `--no-reset`, Q4) → single-reconnect-on-loss.
- `driver/scope.py` — `Scope` methods: `identify`, `configure_channel`,
  `configure_logic`, `configure_trigger`, `capture`, `measure`, `screenshot`,
  `read_waveform`, `decode_bus`, `raw_scpi`. Each pre-validates (line 1 of
  defense), emits SCPI via dialect, and checks `:SYSTem:ERRor?` after
  state-changing ops (line 3).

Tests: every `Scope` method end-to-end against `SimulatedTransport`; validation
rejects out-of-range inputs with actionable errors; `--no-reset` suppresses
`*RST`; reconnect path.

Acceptance: full scope control works against the simulator with zero hardware.

---

## Phase 5 — MCP layer (`mcp/`)

**Goal:** expose the driver as MCP tools with JSON Schema and actionable error
translation; sync/async dispatch.

Deliverables:
- `mcp/tools.py` — the 9 tools (§6 schemas): `get_identity`, `configure_channel`,
  `configure_logic`, `configure_trigger`, `capture`, `measure`, `screenshot`,
  `read_waveform`, `decode_bus`, plus `scpi_raw` registered **only** when
  `--allow-raw-scpi` (Q3). Each validates input against schema, calls the driver,
  catches `SkippyError` → MCP `isError` with the actionable message; wraps
  unexpected exceptions generically and logs a traceback.
- `mcp/server.py` — wiring + `main()` entry point; sync dispatch by default,
  thread-executor dispatch under `--async` (§7.1). Logging via the project logger
  (no bare stdio).

Tests: each tool's input validates against its schema; error mapping produces the
actionable contract; `scpi_raw` absent unless flag set; `screenshot` returns MCP
image content; sync and async dispatch both serve a call.

Acceptance: a local MCP client can drive the simulated scope end-to-end.

---

## Phase 6 — Integration & contract tests

**Goal:** prove the whole stack against the simulator.

Deliverables:
- End-to-end scenario test: connect → configure CH1 → single capture → measure
  Vpp → screenshot, all via the MCP tool layer against `SimulatedTransport`.
- Contract tests: every tool input validates against its JSON Schema; every
  `SkippyError` satisfies the operation/input/reason/check contract.
- README usage section (config flags, example client invocation).

Acceptance: `pytest` green end-to-end with no hardware; `mypy --strict` and
`ruff` clean across the package.

---

## Phase 7 — Live hardware smoke test (DEFERRED — needs MSO5204 on LAN)

**Goal:** validate the round trip against the real instrument.

Steps (run when the scope is powered and on the network):
1. Note scope IP (Utility → IO).
2. `skippy-mcp --host <ip>` → `get_identity` confirms MSO5204 + MSO5000 dialect.
3. `configure_channel` CH1, `screenshot` → verify a real PNG comes back.
4. Swap `SimulatedTransport` expectations for any real-SCPI surprises; fix the
   dialect/parsers as needed.

Acceptance: the §9 PoC from `20260605A` passes against live hardware.

---

## Phase 8 — Packaging & docs

- Finalize `README.md` (install, configure, tool reference, examples).
- Optional Docker image (pure-Python `pyvisa-py`, container-friendly per design).
- Tag `v0.1.0` (annotated, signed) once Phase 7 passes — **only on Mark's say-so**.

---

## Dependency / ordering summary

```
0 scaffold → 1 core → 2 transport(+simulator) → 3 dialect → 4 driver/CLI
          → 5 mcp tools → 6 integration  ──(hardware available)──▶ 7 live smoke → 8 package
```

Phases 1–6 are fully parallel-safe to develop in order with no scope. Phase 7 is
the only hardware gate; Phase 8 closes out.

---

## Risks / watch-items

- **Binary-block parsing** (waveform/screenshot IEEE-488.2 `#N...`) — isolate in
  `PyVisaTransport`; cover with simulator fixtures.
- **Real-SCPI drift** — the simulator encodes our *assumptions*; Phase 7 may
  reveal MSO5204 quirks (response formatting, screenshot encoding). Budget fix
  time there.
- **MCP SDK choice/version** — pin in Phase 0; confirm image-content support for
  `screenshot`.
- **Deep-memory waveforms** — `read_waveform` must chunk/downsample; guard with a
  `max_points` cap and log truncation rather than silently capping.

---

## What I will NOT do until you approve this plan

No code, no `.venv`, no `pyproject.toml`, no commits beyond this plan document.
On approval, I will start at Phase 0 and proceed phase-by-phase, running tests
after each.
