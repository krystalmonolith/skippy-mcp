# SkippyMCP — Initial Design

**Document:** `20260605A-skippy-mcp-initial-design.md`
**Date:** 2026-06-05
**Status:** Initial design — named and scoped, implementation not yet started.

---

## 1. Overview

**SkippyMCP** is an [MCP](https://modelcontextprotocol.io) (Model Context Protocol)
server that lets an AI assistant (Claude Code) drive a Rigol bench oscilloscope:
configure setups, arm captures, take measurements, grab screenshots, pull raw
waveform data, and read protocol-decode results.

- **Project name:** SkippyMCP
- **Executable wrapper:** `skippy-mcp`
- **Name origin:** SCPI — the instrument command protocol — is pronounced
  *"skippy"* in the test-and-measurement world. A server whose entire job is
  speaking SCPI to a scope names itself.

---

## 2. Target Instrument

Primary target is the bench's Rigol **MSO5204** (MSO5000 series, UltraVision II).
Full specifications are in [Appendix A](#appendix-a-instrument-specifications).

Relevant capabilities the design exercises:

- 4 analog channels, 200 MHz bandwidth, 8 GSa/s.
- **16 digital logic channels** via the `:LA:` SCPI subsystem (mixed-signal).
- Hardware **bus decode** via `:BUS<n>:` — I2C, SPI, UART/RS232, Parallel,
  CAN, LIN.
- Remote control over **LAN** (raw socket TCP 5555 / VXI-11 / LXI) and
  **USB-TMC**.

---

## 3. Protocol & Transport Background

Three layers stack to reach the instrument:

1. **SCPI** (Standard Commands for Programmable Instruments) — the plain-text
   command language the scope speaks. Writes (e.g. `:CHANnel1:SCALe 0.1`) and
   queries (e.g. `:MEASure:VPP? CHANnel1` → a value). This is *what* is sent.
2. **VISA** (Virtual Instrument Software Architecture) — the transport
   abstraction. Hides whether bytes travel over USB-TMC, TCP socket, GPIB, or
   serial behind a uniform `write()` / `read()` / `query()` API. Instruments are
   addressed by a resource string, e.g.
   `TCPIP0::192.168.1.50::INSTR` (LAN) or
   `USB0::0x1AB1::0x0515::<serial>::INSTR` (USB; `0x1AB1` is Rigol's vendor ID).
   This is *how* the bytes travel.
3. **PyVISA** — the Python binding to the VISA API, driving a backend. SkippyMCP
   uses the **`pyvisa-py`** pure-Python backend (no vendor VISA install required,
   container-friendly).

---

## 4. Architecture

```
Claude  →  MCP tools  →  SCPI strings  →  PyVISA  →  pyvisa-py backend  →  TCP/USB  →  MSO5204
```

The MCP server is a thin SCPI translator plus a few smart helpers. The MCP tool
calls translate into SCPI strings pushed through a single PyVISA `Resource`.

### 4.1 Closed-loop operation (the killer feature)

The value is in the feedback loop, not remote keystrokes:

```
write setup  →  trigger capture  →  pull screenshot + numeric measurements back  →  observe + iterate
```

Example: "Vpp reads 0 — channel is probably off-screen; autoscale and
re-measure." This is the same pattern as the existing TUI screenshot workflow:
act, observe the rendered result and the numbers, converge.

### 4.2 Division of labor / latency

SkippyMCP is **not** real-time and does not need to be. The **scope hardware**
performs the fast trigger and capture (including single-shot transients); the
server sets up the acquisition and then **polls** for results. No microsecond
reactivity is required of the AI or the server.

### 4.3 Per-series dialect layer

Build a common tool surface on top of a small **dialect layer** — a dictionary
of SCPI strings per scope series, selected at connect time by parsing `*IDN?`.
Target the **MSO5000** dialect first; adding DHO800 / DS1000Z / MSO1000Z / etc.
later is mostly mapping ~a dozen command strings, not rewriting the server. This
avoids the one-model limitation of existing tools.

---

## 5. MCP Tool Surface

Hybrid design: high-level semantic tools for common operations, plus one gated
raw escape hatch for the long tail.

| Tool | Purpose | Representative SCPI |
|------|---------|---------------------|
| `configure_channel` | Analog channel: enable, V/div, offset, coupling, bandwidth limit, probe ratio | `:CHANnel<n>:SCALe`, `:CHANnel<n>:OFFSet`, `:CHANnel<n>:COUPling` |
| `configure_logic` | Digital channels D0–D15: enable groups, thresholds, labels | `:LA:DIGital<n>:DISPlay`, `:LA:DIGital<n>:THReshold` |
| `configure_trigger` | Edge / pulse-width / **pattern across analog + digital**, slope, level, mode | `:TRIGger:MODE`, `:TRIGger:EDGE:...`, `:TRIGger:PATTern:...` |
| `capture` | Run / Stop / **Single** acquisition control | `:RUN`, `:STOP`, `:SINGle` |
| `measure` | Automated measurements: Vpp, Vrms, freq, period, duty, rise/fall, delay/phase | `:MEASure:ITEM? <item>,<src>` |
| `screenshot` | Capture live screen image | `:DISPlay:DATA? PNG` |
| `read_waveform` | Raw sample array (IEEE-488.2 binary block; chunked for deep memory) | `:WAVeform:DATA?` |
| `decode_bus` | Read hardware bus-decode results (I2C/SPI/UART/Parallel/CAN/LIN) | `:BUS<n>:...` |
| `scpi_raw` | **Gated** escape hatch: send an arbitrary SCPI command/query | (any) |

### 5.1 Implementation notes

- **`query()` = `write()` + `read()`.** It is the workhorse for command/response.
- **Termination characters** — SCPI lines terminate with `\n`; PyVISA manages
  `read_termination` / `write_termination`.
- **Binary blocks** — bulk data (waveforms, screenshots) returns in IEEE-488.2
  definite-length block format: `#` + one digit (length-of-length) + byte count +
  raw bytes (e.g. `#9000524288<...>`). Use `query_binary_values()` to parse the
  header. This is the one genuinely fiddly part.
- **Blocking I/O** — `query` waits for the reply or times out; acceptable under
  the setup-then-poll model.
- **`scpi_raw` is powerful** and must be gated carefully — it bypasses the
  semantic safety of the typed tools.

---

## 6. Compatible Rigol Models

The transport layer (PyVISA over LAN/USB) is **universal** across modern Rigol
DSO/MSO families. What varies is the SCPI dialect and which features exist
(digital channels + bus decode only on MSO/logic-pod variants).

| Series | BW / class | Digital (MSO) | Notes |
|--------|-----------|---------------|-------|
| **MSO5000 / DS5000** | 70–350 MHz | 16 (MSO) | **Primary target.** Best-documented; full `:LA:` + `:BUS:`. |
| **DHO800 / DHO900** | 70–250 MHz | 16 (variants) | Newest 12-bit series; existing `aimoda` MCP targets DHO824. |
| **MSO1000Z / DS1000Z** | 50–100 MHz | 16 (MSO1000Z) | Very popular (DS1054Z); `Rigol1000z` lib targets this dialect. |
| **MSO2000A / DS2000A** | 70–300 MHz | 16 (MSO) | Older, fully SCPI/LAN capable. |
| **MSO4000 / DS4000(E)** | 100–500 MHz | 16 (MSO) | Programming guide available. |
| **MSO7000** | 100–500 MHz | 16 | UltraVision II; dialect close to MSO5000. |
| **MSO8000** | 350 MHz–2 GHz | 16 | High-end; SCPI/LXI. |

---

## 7. Prior Art

| Project | Relevance |
|---------|-----------|
| [`aimoda/rigol-dho824-mcp`](https://github.com/aimoda/rigol-dho824-mcp) | Closest direct match: MCP server for Rigol DHO824. Python, PyVISA, MIT, Docker. **DHO800-only, analog-only — no logic/decode.** |
| [`KenosInc/sigrok-mcp-server`](https://github.com/KenosInc/sigrok-mcp-server) | MCP over `sigrok-cli`; logic-analyzer capture + 100+ protocol decoders. Useful for the digital/decode half if not hand-rolled. |
| [`wegitor/logic-analyzer-ai-mcp`](https://github.com/wegitor/logic-analyzer-ai-mcp) | MCP for Saleae Logic devices; reference design for capture automation. |
| [`jeanyvesb9/Rigol1000z`](https://github.com/jeanyvesb9/Rigol1000z) | Python VISA lib for DS1000Z series. Different dialect — **not reusable** for MSO5000, but a reference. |
| [PyVISA](https://github.com/pyvisa/pyvisa) / [python-ivi](https://github.com/python-ivi/python-ivi) | Foundational instrument-control libraries. |

**Conclusion:** No existing project covers Rigol **MSO + 16-channel logic +
bus decode** through MCP. SkippyMCP fills that gap, targeting MSO5000 first with
a dialect layer for later expansion.

---

## 8. Limits & Non-Goals

- **Not real-time.** The server cannot react in microseconds; the scope's trigger
  hardware does the fast work. Setup-then-poll only.
- **Physical world is manual** — probe connections, ground leads, probe
  compensation, attenuation switches.
- **Per-model SCPI drift** — screenshot format and some mnemonics differ between
  series; the dialect layer absorbs this.
- **Binary-block parsing / chunked deep-memory reads** require care but are
  well-trodden.

---

## 9. Proposed First Milestone

Validate the round trip before building the full surface:

1. Connect over LAN via PyVISA / `pyvisa-py`.
2. `*IDN?` — confirm identity / dialect selection.
3. Configure one analog channel.
4. Capture a screenshot (`:DISPlay:DATA? PNG`) and return it.

Once the round trip is proven against the real MSO5204, build out the remaining
tools.

---

## Appendix A — Instrument Specifications

Bench oscilloscope targeted by SkippyMCP. Machine-specific identifiers
(serial number, MAC address) are intentionally omitted.

| Field | Value |
|-------|-------|
| Manufacturer | Rigol Technologies |
| Model | **MSO5204** |
| Series | MSO5000 (UltraVision II) |
| Max bandwidth | 200 MHz |
| Analog channels | 4 (CH1–CH4) |
| Digital channels | 16 (D0–D15, logic analyzer) |
| Max sample rate | 8 GSa/s |
| Bus decode | I2C, SPI, UART/RS232, Parallel, CAN, LIN (hardware) |
| Remote interfaces | LAN (raw socket TCP 5555 / VXI-11 / LXI), USB-TMC |
| Firmware | 00.01.03.02.02 |
| Hardware revision | 01.01.000 |
| Boot version date | 2018.06.27 |
| Build date | 2022-12-05 |
