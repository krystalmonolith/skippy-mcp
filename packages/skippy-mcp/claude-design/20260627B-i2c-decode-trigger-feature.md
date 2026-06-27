# SkippyMCP feature scope — first-class I2C decode config + I2C trigger

**Status:** scoped (not implemented). **Target:** SkippyMCP `0.4.0`.
**Date:** 2026-06-27.

## 1. Motivation

Today SkippyMCP can *read* an I2C decode (`decode_bus` sets `:BUS:MODE IIC`,
enables the decoder, reads `:BUS:DATA?`) but it cannot **configure** the decoder
(which channel is SCL vs SDA, thresholds, address mode) — `BusConfig.options` is
accepted but reported `"unimplemented"`. And `configure_trigger` implements only
`edge`, so there is no way to trigger on an I2C event (START / address / data).

Net effect for "monitor I2C between two devices": the bus decoder must be wired
up on the front panel (or via the gated `scpi_raw` escape hatch) before SkippyMCP
is useful. This feature closes both gaps so the whole flow is driveable from
structured MCP tools.

## 2. Verified SCPI (live MSO5204, firmware 00.01.03.02.02)

These were confirmed empirically against the connected MSO5204 by sending each
candidate and checking `:SYSTem:ERRor?` (header error ⇒ wrong keyword). **Do not
"clean up" the spellings — the decode and trigger subsystems are deliberately
asymmetric on this firmware.**

> **⚠ Hardware status (2026-06-27): serial decode is currently DISABLED on the
> test unit.** The §2 tables confirm the sub-command *headers parse* — they do
> NOT prove the scope enters a serial mode, and right now it does not.
> `:BUS<n>:MODE IIC|SPI|RS232|CAN|LIN` is refused (`-200 "Command execute
> failed"`, or silently ignored) and `:BUS<n>:MODE?` stays `PAR`; only
> `:BUS<n>:MODE PARallel` (the license-free built-in) engages. The scope exposes
> no option-listing SCPI (`*OPT?` / `:SYSTem:OPTion?` → `-100`), so the license
> state is only visible on the front panel at **Utility → System → Help → Option
> list**. This is the known MSO5000 pattern where a firmware update resets the
> serial-decode entitlement (must be re-applied per firmware version). **Net:
> Phase 1 is complete and simulator-validated; the live decode/trigger E2E (§4.1
> Tier A/B) is gated on re-enabling the decode option.**

### 2.1 Decode (`:BUS<n>:…`, n ∈ {1,2})

| Purpose | Command | Notes |
|---|---|---|
| Protocol | `:BUS<n>:MODE IIC` | |
| SCL source | `:BUS<n>:IIC:SCLK:SOURce <src>` | **`SCLK`**, with `:SOURce`. `:IIC:SCL:SOURce` → header error. |
| SDA source | `:BUS<n>:IIC:SDA:SOURce <src>` | |
| SCL threshold | `:BUS<n>:IIC:SCLK:THReshold <v>` | volts |
| SDA threshold | `:BUS<n>:IIC:SDA:THReshold <v>` | volts |
| Address mode | `:BUS<n>:IIC:ADDRess NORMal\|RW` | `RW` folds the R/W bit into the address |
| Display | `:BUS<n>:DISPlay ON\|OFF` | enable the decode row |
| Format | `:BUS<n>:FORMat HEX\|…` | output radix |
| Read decode | `:BUS<n>:DATA?` | IEEE block; **already used by SkippyMCP and correct** |
| Event count | `:BUS<n>:EVENt?` | integer count of decoded events |

`<src>` tokens are the dialect's `scpi_source()` output: `CHANnel1..4` or `D0..D15`.

`:BUS<n>:DATA?` returns a definite-length block whose payload is CSV-ish:
```
DECODE1,
Time,Data,,
<time>,<data>,,
...
```
(The current `_parse_frames` already consumes this.)

### 2.2 Trigger (`:TRIGger:IIC:…`)

| Purpose | Command | Notes |
|---|---|---|
| Mode | `:TRIGger:MODE IIC` | |
| SCL source | `:TRIGger:IIC:SCL <src>` | **no `SOURce` suffix** — `:IIC:SCLSource` → header error |
| SDA source | `:TRIGger:IIC:SDA <src>` | likewise no suffix |
| Condition | `:TRIGger:IIC:WHEN STARt\|ADDRess\|…` | `STARt`, `ADDRess` confirmed; `RESTart`/`STOP`/`NACKnowledge`/`DATA`/`ADAT` expected — verify before relying |
| Address width | `:TRIGger:IIC:AWIDth 7\|…` | `7` confirmed; `10` expected |
| Address | `:TRIGger:IIC:ADDRess <n>` | |
| Direction | `:TRIGger:IIC:DIRection WRITe\|…` | `WRITe` confirmed; `READ`/`RWRite` expected |
| Data | `:TRIGger:IIC:DATA <n>` | |
| Clock level | `:TRIGger:IIC:CLEVel <v>` | analog-source trigger level |
| Data level | `:TRIGger:IIC:DLEVel <v>` | analog-source trigger level |

**Key asymmetry to encode in the dialect:** decode uses `SCLK`/`SDA` **with**
`:SOURce`; trigger uses `SCL`/`SDA` **without** a suffix. This is the single most
important reason this was probed live rather than guessed.

> Probe artifact note: `:BUS:DATA?` returns a large (~77 KB) block; the live probe
> reader must drain the full block (byte-buffer to the trailing `:SYSTem:ERRor?`
> line) or every subsequent read desyncs. Relevant if extending the probe.

## 3. Design — layer by layer (mirrors the existing cake)

### enums (`core/enums.py`)
- `TriggerMode`: add `I2C = ("i2c", "IIC")`.
- New: `I2cAddressMode` (`normal`→`NORMal`, `rw`→`RW`); `I2cTriggerWhen`
  (`start`→`STARt`, `restart`→`RESTart`, `stop`→`STOP`, `nack`→`NACKnowledge`,
  `address`→`ADDRess`, `data`→`DATA`, `addr_data`→`ADAT`); `I2cDirection`
  (`write`→`WRITe`, `read`→`READ`, `either`→`RWRite`). All `ScpiEnum`.
- Optional: `BusFormat` (`hex`/`dec`/`bin`/`ascii`).

### models (`core/models.py`)
- `BusConfig`: replace the opaque `options: dict[str,str]` with typed, optional,
  protocol-specific fields. For I2C: `scl_source`, `sda_source`,
  `scl_threshold_v`, `sda_threshold_v`, `address_mode: I2cAddressMode | None`,
  `display: bool | None`, `fmt: BusFormat | None`. (Keep a generic `options`
  passthrough for forward-compat / other protocols, still echoed as
  `unimplemented` when used.)
- `TriggerConfig`: add I2C fields: `scl_source`, `sda_source`, `when:
  I2cTriggerWhen | None`, `address: int | None`, `address_width: int | None`,
  `direction: I2cDirection | None`, `data: int | None`, `clock_level_v`,
  `data_level_v`. (Which apply depends on `mode`; the driver validates.)

### dialect (`dialect/base.py` + `dialect/mso5000.py`)
- Base: add the new bus/trigger method signatures, defaulting to
  `NotSupportedError` (consistent with the existing `bus_*` methods, so non-MSO
  dialects degrade cleanly).
- mso5000: implement them with the **verified** strings from §2, e.g.
  - `bus_iic_scl_source(n, src)` → `:BUS{n}:IIC:SCLK:SOURce {scpi_source(src)}`
  - `bus_iic_sda_source(n, src)` → `:BUS{n}:IIC:SDA:SOURce {…}`
  - `bus_iic_scl_threshold(n, v)` / `bus_iic_sda_threshold(n, v)`
  - `bus_iic_address_mode(n, mode)` → `:BUS{n}:IIC:ADDRess {mode.scpi}`
  - `bus_format(n, fmt)` → `:BUS{n}:FORMat {fmt.scpi}`
  - `trigger_iic_scl(src)` → `:TRIGger:IIC:SCL {scpi_source(src)}`  *(no SOURce)*
  - `trigger_iic_sda(src)` → `:TRIGger:IIC:SDA {…}`
  - `trigger_iic_when(w)` / `_awidth(n)` / `_address(a)` / `_direction(d)` /
    `_data(x)` / `_clevel(v)` / `_dlevel(v)`.

### driver (`driver/scope.py`)
- `decode_bus`: after `:BUS:MODE IIC`, apply the configured sources / thresholds /
  address-mode / format (only the fields that are set), then `:DISPlay ON`, then
  read `:BUS:DATA?`. `_check_errors("decode_bus")` after the writes.
- `configure_trigger`: add a `mode is TriggerMode.I2C` branch writing the
  `:TRIGger:IIC:*` sub-commands; validate combinations (e.g. `when=address`
  requires `address`; `data`/`addr_data` requires `data`; analog source ⇒ levels
  meaningful, digital source ⇒ thresholds come from `:LA:` / decode thresholds).
  Raise actionable `ValidationError` on bad combos.

### tools (`mcp/tools.py`)
- `decode_bus` schema: replace the free-form `config` with a typed `i2c` object
  (`scl_source`, `sda_source`, `scl_threshold_v`, `sda_threshold_v`,
  `address_mode`, `format`). Unknown keys still echo `unimplemented`.
- `configure_trigger` schema: add the I2C fields, meaningful when `mode:"i2c"`.
- Handlers translate args → `BusConfig` / `TriggerConfig`; return the existing
  `{status,…}` / decoded `{frames[]}` shapes.

### simulator (`transport/simulated.py`)
- Accept the new SCPI writes into `history` (so unit tests assert the exact
  emitted sequence) and return a canned `:BUS:DATA?` block so `decode_bus` round
  trips with **no hardware**. This keeps the whole feature CI-testable.

## 4. Testing

- **Unit (simulator, no hardware):**
  - `decode_bus` with an `i2c` config emits the exact verified sequence in order
    (`MODE IIC` → SCLK/SDA sources → thresholds → address-mode → `DISPlay ON` →
    `DATA?`); `options` no longer reported `unimplemented` for known keys.
  - `configure_trigger` `mode=i2c` emits `:TRIGger:MODE IIC` + the right
    `:TRIGger:IIC:*`; bad combos raise `ValidationError`.
  - enum/schema round-trips; the decode/trigger source-spelling asymmetry is
    locked in by asserting the literal SCPI.
- **Live E2E (MSO5204):**
  - Phase 0 keyword pin-down (done; see §2).
  - Assign two pod channels as SCL/SDA via the new config, set an I2C trigger,
    `single`, then `decode_bus` → frames. Two test sources (§4.1).

### 4.1 Test sources

The rig's `DATA_PINS` are general GPIO driven push-pull as a *parallel group* —
**not** the Pi's hardware I2C peripheral, and StimpyMCP is not an I2C master (it
bangs a 17-bit word per frame). The Pi's hardware I2C-1 (`GPIO2`=SDA, `GPIO3`=SCL)
is **free** — not in `rig_contract.DATA_PINS` (which start at BCM 4) and referenced
nowhere — so the I2C bench and the 16-bit stimulus rig **coexist on rigelpi with no
pin conflict**. Two tiers:

| | **Tier A — synthetic (reuse rig as-is)** | **Tier B — real bus (ESP32 slave)** |
|---|---|---|
| Source | StimpyMCP frames on 2 existing data lines (e.g. D0=SCL, D1=SDA), I2C bit-pattern encoded into the frame buffer at tick ≈ ¼ bit-period | Pi `i2c-1` master (`smbus`/`i2c-tools`) ↔ ESP32 I2C slave with programmable registers |
| Wiring | none — MSO pod already on those lines | `GPIO2`/`GPIO3` + 3.3 V pull-ups → ESP32 **and** tapped into 2 MSO pod channels; common GND; enable `dtparam=i2c_arm=on` |
| Levels | push-pull 3.3 V (not true open-drain) | real open-drain + pull-ups, 3.3 V both ends (no level shifter; **don't** hang a 5 V part on it) |
| Validates | decode SCPI path; START / address / write-data **triggers** (all master-driven) | full decode realism; **read-direction**, real **ACK/NACK**, clock-stretch; resolves open-question §6 (digital-source threshold ownership) |
| Cost | free, today | ~1 ESP32 + 2 resistors (~4.7 kΩ) + jumpers + 2 pod taps |

**Phase mapping:** Tier A suffices for Phase 1 (decode config) and the
master-driven Phase 2 triggers. Tier B is the gold-standard E2E for read-data,
ACK/NACK realism, and pinning the threshold-ownership question — ESP32-as-slave
beats a fixed sensor because its register responses/NACKs can be programmed to hit
specific trigger conditions.

## 5. Phasing

| Phase | Scope |
|---|---|
| **0** ✅ | Pin exact MSO5000 I2C SCPI against the live scope (§2) |
| **1** ✅ (sim) | `decode_bus` I2C config (sources + thresholds + address-mode + format) — implemented + simulator-validated; live E2E gated on decode option (§2 status) |
| **2** | I2C trigger (`mode=i2c` + when/address/data/direction/levels) |
| **3** *(opt)* | Structured I2C frames (address, R/W, ack/nack, data bytes) in decode output |
| **4** *(future)* | Same option/trigger pattern for SPI / UART / CAN |

Phases 1 and 2 are each a focused session. Ship as a **minor bump `0.4.0`** with a
signed tag (core source change).

## 6. Risks / open questions

- **Threshold ownership for digital sources:** when SCL/SDA are `D0..D15`, the
  effective threshold may come from the LA pod (`:LA:` / per-pod threshold) rather
  than `:BUS:IIC:…:THReshold`. Verify which actually moves the decode on digital
  sources; `:…:THReshold` is confirmed *accepted* but its effect on digital lines
  is unconfirmed.
- **`WHEN` / `DIRection` / `AWIDth` value sets:** only `STARt`/`ADDRess`, `WRITe`,
  `7` were confirmed; the rest are expected from the programming guide — verify
  before exposing them as enum values.
- **Trigger levels** (`CLEVel`/`DLEVel`) apply to analog sources; for digital
  sources they're inert — the tool schema/validation should reflect that.
- **Scope of `options` passthrough:** keep the generic escape valve or go fully
  typed? Recommendation: typed I2C fields now, keep `options` as a documented
  passthrough echoing `unimplemented` so non-I2C protocols don't regress.
