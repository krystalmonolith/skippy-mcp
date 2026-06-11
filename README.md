# SkippyMCP

An MCP server that drives a Rigol **MSO/DSO oscilloscope** over SCPI/PyVISA, letting
an AI assistant configure the scope, run captures, and read measurements. This repo
is a small monorepo: **SkippyMCP is the project** — the stimulus generator and the
shared contract below exist to exercise and validate it.

| Package | What it is | Runs on |
|---|---|---|
| [`skippy-mcp`](packages/skippy-mcp) | **SkippyMCP** — Rigol oscilloscope control (SCPI/PyVISA) | a network client of the scope |
| [`stimpy-mcp`](packages/stimpy-mcp) | **StimpyMCP** — GPIO digital-stimulus generator (pigpio); a general-purpose pattern source whose job here is to feed SkippyMCP's logic-analyzer path | a Raspberry Pi |
| [`rig-contract`](packages/rig-contract) | Shared pin map + pattern vector (no hardware deps) | imported by both |

`rig-contract` is the single source of truth tying the two together: StimpyMCP drives
the pattern out of the Pi's GPIO, and SkippyMCP asserts the captured logic words
against the *same* `rig_contract.PATTERN` — a closed loop.

> **Scope:** SkippyMCP is the headline server. StimpyMCP is subordinate test tooling
> — a pattern generator that earns its keep by exercising Skippy; if it ever grows
> beyond that role it will fork to its own project.

## Develop

```bash
python3 -m venv .venv
.venv/bin/pip install -e packages/rig-contract
.venv/bin/pip install -e "packages/stimpy-mcp[dev]"
.venv/bin/pip install -e "packages/skippy-mcp[dev]"

# Per-package checks (mirror CI). The server suites need no hardware — StimpyMCP
# runs on an in-memory simulator, SkippyMCP on a simulated transport:
cd packages/stimpy-mcp        # or rig-contract, or skippy-mcp
../../.venv/bin/python -m pytest -q
../../.venv/bin/ruff check src tests
../../.venv/bin/mypy
```

## Layout

```
packages/
  skippy-mcp/     src/skippy_mcp/             Rigol SCPI control (the project)
                  reference/logic-analyzer/   scope-side capture oracle (bring-up scripts)
  stimpy-mcp/     src/stimpy_mcp/             core -> engine -> driver -> mcp (Pi stimulus)
  rig-contract/   src/rig_contract/           pins + PATTERN + counter helpers (shared)
.github/workflows/ci.yml                      per-package matrix
```
