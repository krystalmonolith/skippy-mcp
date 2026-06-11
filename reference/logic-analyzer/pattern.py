"""Shared digital-stimulus contract for SkippyMCP logic-channel testing.

This module is the single source of truth shared by:

  * ``stimulus.py``      -- runs on the Raspberry Pi, drives the GPIO lines.
  * the SkippyMCP tests  -- import :data:`PATTERN` as the *expected* capture
    vector and assert it against what the MSO5204 reports over ``:LA:``.

It has **no** Raspberry Pi dependencies (it never imports ``gpiod``), so it is
importable anywhere -- including CI on a machine with no GPIO hardware.

Channel mapping (Rigol RPL2316 logic pod <-> Pi 4 BCM GPIO, wired through the
Adafruit 40-pin T-Cobbler Plus -- clip each pod grabber to the breadboard pin
whose silkscreen prints the matching BCM number)::

    D0  -> BCM 4     D8  -> BCM 19
    D1  -> BCM 5     D9  -> BCM 20
    D2  -> BCM 6     D10 -> BCM 21
    D3  -> BCM 12    D11 -> BCM 22
    D4  -> BCM 13    D12 -> BCM 23
    D5  -> BCM 16    D13 -> BCM 24
    D6  -> BCM 17    D14 -> BCM 25
    D7  -> BCM 18    D15 -> BCM 26
    SYNC -> BCM 27   GND(pod) -> any GND pin

Levels are 3.3 V CMOS -- set the pod threshold to ~1.4 V (TTL). A common
ground between the pod and the Pi is mandatory.
"""

from __future__ import annotations

# BCM GPIO line offsets on /dev/gpiochip0 (the Pi 4 40-pin header), indexed by
# logic-pod channel: DATA_PINS[i] drives pod channel D<i>.
DATA_PINS: tuple[int, ...] = (
    4, 5, 6, 12, 13, 16, 17, 18,      # D0..D7
    19, 20, 21, 22, 23, 24, 25, 26,   # D8..D15
)
SYNC_PIN: int = 27

CHANNEL_COUNT: int = len(DATA_PINS)   # 16
_MASK: int = (1 << CHANNEL_COUNT) - 1  # 0xFFFF

# Index within PATTERN at which the SYNC line is asserted (loop-start marker).
SYNC_FRAME: int = 0


def _walking_one() -> list[int]:
    """One channel high per frame -- proves D<n> maps to the right line."""
    return [1 << i for i in range(CHANNEL_COUNT)]


def _walking_zero() -> list[int]:
    """One channel low per frame -- catches a line stuck high."""
    return [(~(1 << i)) & _MASK for i in range(CHANNEL_COUNT)]


def build_pattern() -> list[int]:
    """Return the looping sequence as a list of 16-bit words.

    Bit ``i`` (value ``1 << i``) of each word drives pod channel ``D<i>`` high.
    Frame :data:`SYNC_FRAME` (0) is the all-zero alignment marker; the SYNC
    line pulses there so a scope / SkippyMCP can phase-lock to the loop start.

    Segments, each exercising a distinct failure mode:

    * **markers**   ``0x0000, 0xFFFF, 0xAAAA, 0x5555`` -- stuck bits, plus
      shorts between adjacent lines (``AAAA``/``5555`` are complementary
      alternating patterns; a short collapses them).
    * **walking-1** exactly one channel high -- channel-identity / swap check.
    * **walking-0** exactly one channel low  -- stuck-high detection.
    * **count**     low nibble counts ``0x0..0xF`` while the high byte echoes
      the frame index -- many simultaneous edges for decode/timing stress.
    """
    frames: list[int] = [0x0000, 0xFFFF, 0xAAAA, 0x5555]
    frames.extend(_walking_one())
    frames.extend(_walking_zero())
    for n in range(16):
        frames.append(((n & 0xFF) << 8) | (n & 0x0F))
    return frames


PATTERN: tuple[int, ...] = tuple(build_pattern())
