"""Shared rig contract — the single source of truth for the digital-stimulus rig.

This package is imported by BOTH:

  * **StimpyMCP** (the Pi-side stimulus generator) — the pin map and frame words.
  * **SkippyMCP**'s logic-channel tests — the *expected* capture vector.

It has **no hardware dependencies** (no gpiod/pigpio/pyvisa), so it imports
anywhere, including CI on a machine with no Pi and no scope.

Channel mapping (Rigol RPL2316 logic pod <-> Pi 4 BCM GPIO, wired through the
Adafruit 40-pin T-Cobbler Plus — clip each pod grabber to the breadboard pin
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

Levels are 3.3 V CMOS — set the pod threshold to ~1.4 V (TTL). A common ground
between the pod and the Pi is mandatory.
"""

from __future__ import annotations

__version__ = "0.1.0"

#: BCM GPIO line offsets on /dev/gpiochip0 (Pi 4 40-pin header), indexed by
#: logic-pod channel: DATA_PINS[i] drives pod channel D<i>.
DATA_PINS: tuple[int, ...] = (
    4, 5, 6, 12, 13, 16, 17, 18,      # D0..D7
    19, 20, 21, 22, 23, 24, 25, 26,   # D8..D15
)
SYNC_PIN: int = 27

CHANNEL_COUNT: int = len(DATA_PINS)   # 16
MASK: int = (1 << CHANNEL_COUNT) - 1  # 0xFFFF

#: Frame index within build_pattern() at which SYNC is asserted (loop start).
SYNC_FRAME: int = 0


def _walking_one() -> list[int]:
    """One channel high per frame — proves D<n> maps to the right line."""
    return [1 << i for i in range(CHANNEL_COUNT)]


def _walking_zero() -> list[int]:
    """One channel low per frame — catches a line stuck high."""
    return [(~(1 << i)) & MASK for i in range(CHANNEL_COUNT)]


def build_pattern() -> list[int]:
    """Return the canonical channel-mapping test vector as a list of 16-bit words.

    Bit ``i`` (value ``1 << i``) of each word drives pod channel ``D<i>`` high.
    Frame :data:`SYNC_FRAME` (0) is the all-zero alignment marker. Segments:

    * **markers**   ``0x0000, 0xFFFF, 0xAAAA, 0x5555`` — stuck bits + adjacent-line shorts.
    * **walking-1** exactly one channel high — channel-identity / swap check.
    * **walking-0** exactly one channel low — stuck-high detection.
    * **count**     low nibble counts ``0x0..0xF`` with the frame index echoed
      in the high byte — many simultaneous edges for decode/timing stress.

    No two adjacent frames are equal (every frame boundary is a word change), so
    a logic-analyzer capture can recover the frame clock from signal edges.
    """
    frames: list[int] = [0x0000, 0xFFFF, 0xAAAA, 0x5555]
    frames.extend(_walking_one())
    frames.extend(_walking_zero())
    for n in range(16):
        frames.append(((n & 0xFF) << 8) | (n & 0x0F))
    return frames


#: The canonical test vector (52 frames). SkippyMCP asserts captures against this.
PATTERN: tuple[int, ...] = tuple(build_pattern())


def counter_frames(bits: int = CHANNEL_COUNT) -> list[int]:
    """Return a full binary up-counter as a list of words: ``0 .. 2**bits - 1``.

    D0 (LSB) toggles every frame; D<bits-1> (MSB) every ``2**(bits-1)`` frames.
    """
    if not 1 <= bits <= CHANNEL_COUNT:
        raise ValueError(f"bits must be in 1..{CHANNEL_COUNT}, got {bits}")
    return list(range(1 << bits))


def counter_sync_frames(
    bits: int = CHANNEL_COUNT, sync_period: int = 1 << CHANNEL_COUNT
) -> list[int]:
    """Frame indices where SYNC pulses for a counter buffer (every ``sync_period``)."""
    if sync_period < 1:
        raise ValueError(f"sync_period must be >= 1, got {sync_period}")
    return list(range(0, 1 << bits, sync_period))
