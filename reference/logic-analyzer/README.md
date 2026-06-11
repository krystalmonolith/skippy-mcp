# Digital stimulus generator

A deterministic, infinitely-looping 16-channel digital pattern generator that
runs on a **Raspberry Pi 4** and feeds a Rigol **RPL2316** logic pod, so
SkippyMCP's digital-input (`:LA:`) acquisition/decode code can be asserted
against a *known* vector.

The pattern table in [`pattern.py`](pattern.py) is the **single source of
truth**: the Pi driver emits it, and the SkippyMCP test imports the same
`PATTERN` as its expected capture. `pattern.py` has no GPIO dependency, so it
imports anywhere (including CI with no hardware).

## Wiring (Adafruit 40-pin T-Cobbler Plus)

The T-Cobbler's breadboard pins are silkscreened with bare **BCM** numbers, and
its ribbon connector is keyed -- so just clip each pod grabber to the pin
printed with the matching number. Pin 1 is irrelevant here.

| Pod | BCM | Pod | BCM | Pod | BCM | Pod | BCM |
|----:|:---:|----:|:---:|----:|:---:|----:|:---:|
| D0  | 4   | D4  | 13  | D8  | 19  | D12 | 23  |
| D1  | 5   | D5  | 16  | D9  | 20  | D13 | 24  |
| D2  | 6   | D6  | 17  | D10 | 21  | D14 | 25  |
| D3  | 12  | D7  | 18  | D11 | 22  | D15 | 26  |
| **SYNC** | **27** | | | | | **GND** | any GND pin |

- Levels are **3.3 V CMOS** -- set the pod threshold to ~1.4 V (TTL).
- A **common ground** between pod and Pi is mandatory.
- The BCM map deliberately avoids the I2C (2/3), SPI (7-11), UART (14/15) and
  ID-EEPROM (0/1) pins so those buses stay free.

## Pattern

52 frames per loop (markers -> walking-1 -> walking-0 -> counter). `SYNC`
pulses on frame 0 (the all-zero alignment marker) so a scope or SkippyMCP can
phase-lock to the loop start. Each 16-bit word is presented atomically (one
libgpiod `set_values` call) to minimise inter-channel skew. At the default
50 ms/frame a loop is ~2.6 s.

## Run (on the Pi)

```bash
python3 stimulus.py              # loop forever at 50 ms/frame
python3 stimulus.py --step 10    # faster (10 ms/frame)
python3 stimulus.py --once       # emit one loop and exit (smoke test)
```

No `sudo` needed -- the user must be in the `gpio` group. Requires
`python3-libgpiod` (libgpiod v2), preinstalled on Raspberry Pi OS (trixie).

## Deploy

The canonical copy lives here in the repo; copy the two `.py` files to the Pi
to run them, e.g.:

```bash
rsync -a stimulus.py pattern.py <pi-host>:~/skippy-stimulus/
ssh <pi-host> 'cd ~/skippy-stimulus && python3 stimulus.py'
```
