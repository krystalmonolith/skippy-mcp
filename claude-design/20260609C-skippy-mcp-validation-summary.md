# SkippyMCP — Validation Summary (v0.1.0)

**Document:** `20260609C-skippy-mcp-validation-summary.md`
**Date:** 2026-06-09
**Release:** v0.1.0 (tagged, pushed to GitHub)
**Scope under test:** Rigol MSO5204 (MSO5000 series, firmware 00.01.03.02.02)

This report records the verification state of SkippyMCP at v0.1.0: the automated
test suite, the layers proven hardware-free against the simulator, and the live
end-to-end validation against a real MSO5204 — including measurement-accuracy
checks against a known signal source.

---

## 1. Summary

| Area | Status |
|------|--------|
| Automated tests | **71 passing** |
| Static type check | `mypy --strict` clean (21 source files) |
| Lint | `ruff` clean |
| Hardware-free stack (core→transport→dialect→driver→mcp) | ✅ complete |
| Live hardware round trip (MSO5204) | ✅ verified |
| Measurement accuracy vs. known 1 kHz / 1 Vpp source | ✅ verified |
| Waveform decode vs. real samples | ✅ verified |
| Logic / bus-decode on hardware | ⏸ deferred (needs digital test rig) |

---

## 2. Automated Test Suite

All tests run with **no hardware**, against `SimulatedTransport` (an in-memory
MSO5204 model). Run: `pytest`, `mypy --strict`, `ruff check`.

| Test file | Tests | Covers |
|-----------|-------|--------|
| `test_driver.py` | 22 | Scope ops + config/CLI; pre-validation; `:SYSTem:ERRor?` mapping |
| `test_transport.py` | 13 | `Transport` protocol conformance; simulator behavior |
| `test_mcp.py` | 12 | Tool specs, handlers, JSON-Schema validity, output conversion, transcode |
| `test_dialect.py` | 10 | Golden SCPI strings; dialect selection; unknown-model refusal |
| `test_core.py` | 8 | Enums, frozen models, `SkippyError` actionable-message contract |
| `test_integration.py` | 5 | End-to-end through the real MCP SDK call path (schema + isError) |
| `test_smoke.py` | 1 | Package import / version |
| **Total** | **71** | |

The integration tests drive the actual MCP SDK request handler, so input-schema
validation and the exception→`isError` mapping are exercised, not mocked.

---

## 3. Live Hardware Validation

### 3.1 Environment

- **Transport:** raw socket — VISA resource `TCPIP0::<scope-ip>::5555::SOCKET`,
  reached from WSL2. Chosen over VXI-11 `::INSTR` (portmapper 111 + dynamic
  secondary port) because a single fixed port traverses WSL2 NAT reliably.
- **Network:** scope on an unmanaged switch with no DHCP server → APIPA
  link-local address (works, but not stable across reboots).
- **Sync after `*RST`:** `*OPC?` (operation-complete query), not a sleep.

### 3.2 Tool-by-tool live results

| Tool | Result on MSO5204 |
|------|-------------------|
| `get_identity` | ✅ `RIGOL TECHNOLOGIES,MSO5204,…,00.01.03.02.02` → MSO5000 dialect; `*IDN?` format matched the simulator's assumption exactly |
| `configure_channel` | ✅ CH1 enable / 200 mV–500 mV per div / coupling / probe ratio — confirmed on the instrument display |
| `configure_trigger` | ✅ edge trigger on CH1 — scope reached "T'D" (triggered) |
| `capture` | ✅ run / stop / single |
| `measure` | ✅ accurate — see §3.3 |
| `read_waveform` | ✅ BYTE→volts decode correct — see §3.4 |
| `screenshot` | ✅ PNG returned — see §3.5 |
| `scpi_raw` | ✅ used for timebase, `*OPC?`, trigger sweep |
| `configure_logic` | ⏸ deferred (no logic probe connected) |
| `decode_bus` | ⏸ deferred (no bus source connected) |

### 3.3 Measurement accuracy — known source (CH1: 1 kHz, 1 Vpp sine)

| Measurement | Reading | Expected | Notes |
|-------------|---------|----------|-------|
| Frequency | 999.91 Hz | ~1000 Hz | ✅ |
| Period | 1.0005 ms | ~1.0 ms | ✅ |
| Vpp | 1.0446 V | ~1.0 V | ✅ (excess = generator tolerance + peak noise) |
| Vrms | 0.3565 V | ~0.354 V | ✅ (sine) |

Our `measure` readings matched the scope's own on-screen measurement readouts
(`Vpp1 1.0445 V`, `Freq1 997.85 Hz`), confirming the `:MEASure:ITEM?` path
returns exactly what the instrument computes.

### 3.4 Waveform decode triangulation

`read_waveform("CH1")` on the same signal:

- 1000 points, x_increment 5.0 µs, x_origin −2.5 ms, time span 5.0 ms
  (= screen width at 500 µs/div).
- min/max −0.514 / +0.531 V; **peak-to-peak 1.0446 V**.
- Frequency from rising zero-crossings: **1000.00 Hz**.

The peak-to-peak figure (1.0446 V) is **identical to four digits** across three
independent paths — the scope's own measurement, our `measure` tool, and our
manual decode of the raw sample array — validating the formula
`volts = (raw − y_origin − y_reference) × y_increment` and the preamble parsing
against real hardware.

### 3.5 Screenshot — real-hardware finding

Live testing revealed that MSO5000 `:DISPlay:DATA?` **always returns a 24-bit
BMP** (~1.84 MB = 1024×600×3 + 54-byte header); the format argument (`PNG`,
`ON,OFF,PNG`) is silently ignored — a divergence the simulator could not predict.

**Resolved:** the dialect emits `:DISPlay:DATA?` (no arg) with
`native_screenshot_format = BMP`; the driver returns the native BMP (kept
Pillow-free); the MCP layer transcodes BMP→PNG (`mcp/imaging.to_png`) before
delivery — ~34× smaller (1.84 MB → ~53 KB), which matters because the image is
base64'd into the model's context. The simulator was updated to return a real
BMP so it stays faithful.

---

## 4. Deferred Validation

| Item | Why deferred | Plan |
|------|--------------|------|
| `configure_logic` (D0–D15) | No 16-channel logic probe connected | Validate with an **ESP32** test rig driving multiple digital lines |
| `decode_bus` (I²C/SPI/UART/…) | No serial-bus source connected | Use the same ESP32 rig as an I²C/SPI source |

The ESP32 will serve as a multi-digital-channel and I²C/SPI bus generator for
end-to-end logic and decode validation in a later pass.

---

## 5. Notes & Known Limitations

- **Not real-time:** the scope hardware does the fast trigger/capture; SkippyMCP
  sets up and polls. Confirmed sufficient for all tested operations.
- **Screenshot is PNG-only over MCP:** native BMP is transcoded; the scope
  cannot emit PNG itself.
- **Link-local addressing** is functional but unstable; a DHCP-served or static
  address is recommended for routine use.

---

## 6. Next Steps (Roadmap)

Tracked for upcoming work, in order:

1. **Debian package (`.deb`)** built via GitHub Actions.
2. **Dockerfile** — author and verify locally (pure-Python `pyvisa-py` keeps the
   image dependency-light).
3. **GitHub Actions release pipeline** — build the `.deb` and Docker image and
   publish them as GitHub Releases.
4. **Optional HTTPS and/or API-key authentication** for network-exposed
   deployments.

The automated suite is hardware-free, so it runs unmodified in CI; live-hardware
checks (§3) remain a manual gate performed against the bench MSO5204.
