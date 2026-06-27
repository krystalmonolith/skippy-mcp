# Logic Pod ↔ Raspberry Pi Cobbler Pin-to-Pin Wiring

**Status:** wiring reference (as-built) · **Date:** 2026-06-27

This is the physical wiring map for the digital-stimulus rig: a **Rigol RPL2316**
16-channel logic pod (on an MSO5204) connected to a **Raspberry Pi 4** through an
**Adafruit 40-pin T-Cobbler Plus** on a breadboard.

The **single source of truth** for the logical mapping is
`packages/rig-contract/src/rig_contract/__init__.py` (`DATA_PINS`, `SYNC_PIN`).
This document adds the two columns that aren't in code: the Pi **40-pin header
physical pin number** and the **T-Cobbler silkscreen label**. If `rig_contract`
and this table ever disagree, `rig_contract` wins — update this file.

## Conventions

- **BCM GPIO** = the Broadcom line number (what software uses; lgpio
  `gpiochip0` line offset). The T-Cobbler silkscreen prints these as `#<n>`.
- **Phys pin** = the 1–40 position on the Pi's 40-pin header.
- The T-Cobbler is a 1:1 passthrough — clip each pod grabber to the breadboard
  row whose silk prints the matching BCM number.

## Data channels — pod D0…D15

Pod channel **D`i`** is driven by `DATA_PINS[i]`.
POD1 = D0–D7, POD2 = D8–D15 (the two threshold groups in `capture.py`).

| Pod signal | Pod group | BCM GPIO | Pi phys pin | T-Cobbler silk |
|:----------:|:---------:|:--------:|:-----------:|:--------------:|
| D0  | POD1 | 4  | 7  | #4  |
| D1  | POD1 | 5  | 29 | #5  |
| D2  | POD1 | 6  | 31 | #6  |
| D3  | POD1 | 12 | 32 | #12 |
| D4  | POD1 | 13 | 33 | #13 |
| D5  | POD1 | 16 | 36 | #16 |
| D6  | POD1 | 17 | 11 | #17 |
| D7  | POD1 | 18 | 12 | #18 |
| D8  | POD2 | 19 | 35 | #19 |
| D9  | POD2 | 20 | 38 | #20 |
| D10 | POD2 | 21 | 40 | #21 |
| D11 | POD2 | 22 | 15 | #22 |
| D12 | POD2 | 23 | 16 | #23 |
| D13 | POD2 | 24 | 18 | #24 |
| D14 | POD2 | 25 | 22 | #25 |
| D15 | POD2 | 26 | 37 | #26 |

## SYNC and ground

SYNC is **not** a digital pod channel — it goes to an **analog input** and serves
as the phase-lock edge trigger (`capture.py`: `:TRIGger:EDGE:SOURce CHANnel1`).

| Signal | Scope connection | BCM GPIO | Pi phys pin | T-Cobbler silk |
|:------:|:----------------:|:--------:|:-----------:|:--------------:|
| SYNC | **Analog CH1** probe tip | 27 | 13 | #27 |
| GND  | Pod ground lead(s) **and** CH1 probe ground | — | any GND | GND |

A common ground between the pod and the Pi is **mandatory**. Tie at least one pod
ground lead to a Pi GND pin. The Pi's GND pins are physical
**6, 9, 14, 20, 25, 30, 34, 39**.

## Electrical notes

- Levels are **3.3 V CMOS**. Set the pod threshold to **~1.4 V (TTL)**
  (`:LA:POD1:THReshold 1.4` / `:LA:POD2:THReshold 1.4`).
- SYNC drives CH1; the capture oracle triggers on a positive edge at **1.6 V**
  (`:TRIGger:EDGE:LEVel 1.6`). If CH1 isn't connected or stays below threshold,
  `capture.py` falls back to free-run (AUTO sweep).
- D0 is the highest-toggling line (pattern LSB); keep its lead short.

## Quick-reference — Pi header, sorted by physical pin

Only the pins used by this rig are listed; everything else is unused.

| Phys | BCM | Rig signal |
|:----:|:---:|:----------:|
| 7  | 4  | D0   |
| 11 | 17 | D6   |
| 12 | 18 | D7   |
| 13 | 27 | SYNC |
| 15 | 22 | D11  |
| 16 | 23 | D12  |
| 18 | 24 | D13  |
| 22 | 25 | D14  |
| 29 | 5  | D1   |
| 31 | 6  | D2   |
| 32 | 12 | D3   |
| 33 | 13 | D4   |
| 35 | 19 | D8   |
| 36 | 16 | D5   |
| 37 | 26 | D15  |
| 38 | 20 | D9   |
| 40 | 21 | D10  |

Plus any one GND pin (6/9/14/20/25/30/34/39) to the pod + CH1 grounds.
