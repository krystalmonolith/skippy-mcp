#!/usr/bin/env python3
"""Capture + verify + scope screenshot, looping until we have one PASS and one
FAIL frame, so the intermittent D0 behaviour can be correlated visually.

Reuses the tested capture.py logic; saves the MSO5000 :DISPlay:DATA? BMP of the
exact frozen acquisition that was just verified, as PNG, into the screenshot dir.
"""

from __future__ import annotations

import io
import sys

import pyvisa
from PIL import Image

import capture as C
from pattern import CHANNEL_COUNT

OUTDIR = "/home/mdeazley/workspace/screenshot"


def grab(inst, path: str) -> None:
    bmp = inst.query_binary_values(":DISPlay:DATA?", datatype="B", container=bytearray)
    Image.open(io.BytesIO(bytes(bmp))).save(path)


def main() -> int:
    rm = pyvisa.ResourceManager("@py")
    inst = rm.open_resource(C.DEFAULT_RESOURCE, read_termination="\n",
                            write_termination="\n", timeout=20000)
    got: dict[str, str] = {}
    n = 0
    try:
        while len(got) < 2 and n < 15:
            n += 1
            C.configure(inst, 1.4, 0.5)
            mode = C.capture(inst, use_sync=True, dwell=0.5 * 10 + 1)
            if not mode.startswith("CH1"):
                print(f"run {n}: no CH1 trigger (SYNC missing?) -- skip")
                continue
            chans, xinc, xorig, xref = C.read_channels(inst)
            _, bad, _ = C.verify_phase_locked(chans, xinc, xorig, xref)
            total = sum(bad)
            verdict = "PASS" if total == 0 else "FAIL"
            worst = max(range(CHANNEL_COUNT), key=lambda i: bad[i])
            print(f"run {n}: {verdict}  total_mismatch={total}  "
                  f"per-ch:{[f'D{i}:{bad[i]}' for i in range(CHANNEL_COUNT) if bad[i]]}")
            if verdict not in got:
                path = f"{OUTDIR}/scope_{verdict}.png"
                grab(inst, path)
                got[verdict] = path
                print(f"   saved {path}")
    finally:
        inst.close()
    print("captured:", got)
    return 0 if len(got) == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
