#!/usr/bin/env python3
"""Drive the SkippyMCP digital test pattern on the Raspberry Pi GPIO header.

Runs on a Raspberry Pi (libgpiod v2 / ``python3-libgpiod``). Presents each
16-bit word of :data:`pattern.PATTERN` *atomically* across the 16 data lines
(one ``set_values`` ioctl -> minimal inter-channel skew at the logic pod),
pulses SYNC on the loop-start frame, and loops forever until Ctrl-C.

``sudo`` is **not** required -- the invoking user must be in the ``gpio`` group.

Usage::

    python3 stimulus.py [--step MS] [--chip PATH] [--once]
"""

from __future__ import annotations

import argparse
import sys
import time

import gpiod
from gpiod.line import Direction, Value

from pattern import CHANNEL_COUNT, DATA_PINS, SYNC_PIN

CONSUMER = "skippy-stimulus"


def word_to_values(word: int, *, sync: bool) -> dict[int, Value]:
    """Map a 16-bit pattern word plus SYNC state to a libgpiod value dict."""
    values: dict[int, Value] = {
        pin: (Value.ACTIVE if (word >> i) & 1 else Value.INACTIVE)
        for i, pin in enumerate(DATA_PINS)
    }
    values[SYNC_PIN] = Value.ACTIVE if sync else Value.INACTIVE
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SkippyMCP digital stimulus generator (Raspberry Pi)",
    )
    parser.add_argument(
        "--step", type=float, default=50.0,
        help="per-count dwell time in milliseconds (default: 50)",
    )
    parser.add_argument(
        "--d0-hz", type=float, default=None,
        help="convenience: set the count rate so D0 = this frequency (overrides --step). "
             "D0 toggles every count, so count_rate = 2 x d0_hz.",
    )
    parser.add_argument(
        "--chip", default="/dev/gpiochip0",
        help="gpiochip backing the 40-pin header (default: /dev/gpiochip0)",
    )
    parser.add_argument(
        "--sync-period", type=int, default=256,
        help="SYNC (CH1) pulses one count high every N counts (default: 256 = low-byte wrap)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="emit one full counter cycle and exit (default: count forever)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.d0_hz is not None:
        if args.d0_hz <= 0:
            print("error: --d0-hz must be > 0", file=sys.stderr)
            return 2
        dwell = 1.0 / (2.0 * args.d0_hz)   # D0 toggles each count => count rate = 2*f
    elif args.step > 0:
        dwell = args.step / 1000.0
    else:
        print("error: --step must be > 0 ms", file=sys.stderr)
        return 2

    all_lines = tuple(DATA_PINS) + (SYNC_PIN,)

    settings = gpiod.LineSettings(
        direction=Direction.OUTPUT, output_value=Value.INACTIVE,
    )
    request = gpiod.request_lines(
        args.chip, consumer=CONSUMER, config={all_lines: settings},
    )

    modulus = 1 << CHANNEL_COUNT  # 16-bit binary counter: 0 .. 65535
    pacing = "busy-wait" if dwell < 0.002 else "sleep+spin"
    print(
        f"[stimulus] BINARY COUNTER on D0..D{CHANNEL_COUNT - 1} = BCM {list(DATA_PINS)} "
        f"(D0=LSB, D{CHANNEL_COUNT - 1}=MSB); {dwell * 1e6:.1f} us/count "
        f"-> D0 ~ {1.0 / (2.0 * dwell):,.1f} Hz; SYNC {SYNC_PIN} every {args.sync_period} counts; "
        f"pacing={pacing} on {args.chip}. Ctrl-C to stop.",
        file=sys.stderr,
    )
    count = 0
    next_t = time.perf_counter()
    t_report = next_t
    n_report = 0
    try:
        while True:
            word = count % modulus
            request.set_values(word_to_values(word, sync=(count % args.sync_period == 0)))
            count += 1
            n_report += 1
            # absolute-time pacing: coarse sleep for the bulk, busy-wait the last bit
            next_t += dwell
            slack = next_t - time.perf_counter()
            if slack > 0.002:
                time.sleep(slack - 0.0005)
            while time.perf_counter() < next_t:
                pass
            now = time.perf_counter()
            if now - t_report >= 2.0:
                rate = n_report / (now - t_report)
                print(f"[stimulus] count={count} achieved {rate:,.0f} counts/s "
                      f"(D0 ~ {rate / 2:,.0f} Hz)", file=sys.stderr)
                t_report = now
                n_report = 0
            if args.once and count >= modulus:
                break
    except KeyboardInterrupt:
        print(f"\n[stimulus] stopped at count {count}", file=sys.stderr)
    finally:
        # Drive every line low, then release the request back to the kernel.
        request.set_values({pin: Value.INACTIVE for pin in all_lines})
        request.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
