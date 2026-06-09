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
- **Status:** design complete; implementation not yet started.

## Documentation

| Document | Description |
|----------|-------------|
| [Initial design](claude-design/20260605A-skippy-mcp-initial-design.md) | Overview, architecture, tool surface, compatible models, prior art. |
| [Detailed design](claude-design/20260609A-skippy-mcp-detailed-design.md) | Layered architecture, transport interface + simulator, dialect layer, error model, tool schemas. |
| [Implementation plan](claude-design/20260609B-skippy-mcp-implementation-plan.md) | Phased build plan (hardware-free through Phase 6). |

## License

[MIT](LICENSE) © 2026 Mark Deazley
