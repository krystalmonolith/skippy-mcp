#!/usr/bin/env python3
"""Localize WHERE an intermittent digital mismatch is born -- without assuming
the scope is a perfect reference.

Loops capture+verify until a FAIL is caught, then interrogates the SAME frozen
acquisition three independent ways to pin the fault to a layer:

  1. DOUBLE-READ: read every channel a second time from the identical frozen
     capture. If the two reads DIFFER -> the fault is in the readback/transport
     (pyvisa-py / socket / SCPI), NOT the scope's acquisition or the signal.
  2. RE-VERIFY the second read on its own.
  3. SCREENSHOT the scope's own rendered display. If the display shows the bit
     CORRECTLY but our readback says wrong -> readback decode issue. If the
     display ALSO shows it wrong -> the scope's acquisition front-end (or the
     signal at capture time) is at fault, not our software.

Decision table (for the worst channel D<k>):
  reads differ ............................. transport/readback flaky (our side)
  reads identical + display correct ........ readback decode (our side)
  reads identical + display ALSO wrong ..... scope acquisition / pod / signal
"""

from __future__ import annotations

import io
import time

import pyvisa
from PIL import Image

import capture as C
from pattern import CHANNEL_COUNT

OUT = "/home/mdeazley/workspace/screenshot/scope_FAULT.png"
MAX_RUNS = 30


def main() -> int:
    rm = pyvisa.ResourceManager("@py")
    inst = rm.open_resource(C.DEFAULT_RESOURCE, read_termination="\n",
                            write_termination="\n", timeout=20000)
    try:
        for n in range(1, MAX_RUNS + 1):
            C.configure(inst, 1.4, 0.5)
            mode = C.capture(inst, use_sync=True, dwell=0.5 * 10 + 1)
            if not mode.startswith("CH1"):
                print(f"run {n}: no trigger, skip")
                continue
            chans_a, xinc, xorig, xref = C.read_channels(inst)
            _, bad_a, _ = C.verify_phase_locked(chans_a, xinc, xorig, xref)
            if sum(bad_a) == 0:
                print(f"run {n}: PASS")
                continue

            worst = max(range(CHANNEL_COUNT), key=lambda i: bad_a[i])
            print(f"\nrun {n}: FAIL  worst=D{worst}({bad_a[worst]})  "
                  f"all={[f'D{i}:{bad_a[i]}' for i in range(CHANNEL_COUNT) if bad_a[i]]}")

            # --- Check 1: double-read the SAME frozen acquisition ---
            chans_b, _, _, _ = C.read_channels(inst)
            differ = [i for i in range(CHANNEL_COUNT) if chans_a[i] != chans_b[i]]
            # --- Check 2: re-verify the second read ---
            _, bad_b, _ = C.verify_phase_locked(chans_b, xinc, xorig, xref)
            # --- Check 3: screenshot the scope's own display ---
            bmp = inst.query_binary_values(":DISPlay:DATA?", datatype="B", container=bytearray)
            Image.open(io.BytesIO(bytes(bmp))).save(OUT)

            print(f"  check1 double-read: channels differing between two reads "
                  f"of the SAME frozen capture = {['D'+str(i) for i in differ] or 'NONE'}")
            print(f"  check2 re-verify 2nd read: "
                  f"{[f'D{i}:{bad_b[i]}' for i in range(CHANNEL_COUNT) if bad_b[i]] or 'CLEAN'}")
            print(f"  check3 screenshot saved: {OUT}")

            print("\n  VERDICT:")
            if differ:
                print("   -> reads DIFFER: fault is in READBACK/TRANSPORT (our side: "
                      "pyvisa-py/socket/SCPI), NOT the scope acquisition or signal.")
            elif sum(bad_b) == 0:
                print("   -> reads identical but 2nd verify CLEAN: non-deterministic in our "
                      "verify path -- investigate edge-recovery, not hardware.")
            else:
                print(f"   -> reads identical AND both wrong: inspect scope_FAULT.png. "
                      f"If D{worst} is wrong on the scope's OWN display too, the fault is the "
                      f"scope acquisition/pod/signal; if the display is correct, it's readback decode.")
            return 0
        print(f"no FAIL in {MAX_RUNS} runs -- not reproducing right now.")
        return 1
    finally:
        inst.close()


if __name__ == "__main__":
    raise SystemExit(main())
