# bench-mcp

A monorepo of MCP servers for the test bench, plus the contract they share.

| Package | What it is | Runs on |
|---|---|---|
| [`rig-contract`](packages/rig-contract) | Shared pin map + pattern vector (no hardware deps) | imported by both |
| [`stimpy-mcp`](packages/stimpy-mcp) | **StimpyMCP** — GPIO digital-stimulus generator (pigpio) | the Raspberry Pi |
| `skippy-mcp` | **SkippyMCP** — Rigol oscilloscope control (SCPI/PyVISA) | a network client of the scope |

`rig-contract` is the single source of truth tying the two together: StimpyMCP
drives the pattern out of the Pi's GPIO, and SkippyMCP asserts the captured logic
words against the *same* `rig_contract.PATTERN` — a closed loop.

> **SkippyMCP** lives in its own repo today; folding it in (history-preserving)
> is a separate, deliberate step. Until then this monorepo holds `rig-contract`
> and `stimpy-mcp`.

## Develop

```bash
python3 -m venv .venv
.venv/bin/pip install -e packages/rig-contract
.venv/bin/pip install -e "packages/stimpy-mcp[dev]"

# StimpyMCP CI suite (runs entirely on the in-memory simulator — no Pi needed):
cd packages/stimpy-mcp
../../.venv/bin/python -m pytest -q
../../.venv/bin/ruff check src tests
../../.venv/bin/mypy
```

## Layout

```
packages/
  rig-contract/   src/rig_contract/   pins + PATTERN + counter helpers
  stimpy-mcp/     src/stimpy_mcp/      core -> engine -> driver -> mcp
tools/            scope-side capture oracle for on-Pi E2E (added with the fold-in)
.github/workflows/ci.yml               per-package matrix
```
