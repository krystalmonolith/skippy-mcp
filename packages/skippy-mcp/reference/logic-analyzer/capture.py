#!/usr/bin/env python3
"""Capture the digital stimulus on the MSO5204 and verify it against the contract.

Configures the MSO5000 logic analyzer (D0-D15), grabs a screen-mode capture
while ``stimulus.py`` free-runs on the Pi, reconstructs the per-frame 16-bit
words, and aligns them against :data:`pattern.PATTERN` to prove the channel
mapping (software ch N == physical line N).

SYNC (Pi BCM27 -> scope CH1) is optional: if CH1 carries a clean >Vthr pulse it
is used as the hardware trigger for a phase-locked capture; otherwise we fall
back to AUTO sweep + software alignment via the unique marker frames.

Run from this directory (imports ``pattern``):

    python3 capture.py [--resource ...] [--threshold 1.4] [--timebase 0.5]
"""

from __future__ import annotations

import argparse
import sys
import time

import pyvisa

from pattern import CHANNEL_COUNT, PATTERN

DEFAULT_RESOURCE = "TCPIP0::dso1::5555::SOCKET"


def drain_errors(inst: pyvisa.resources.MessageBasedResource) -> list[str]:
    out: list[str] = []
    for _ in range(40):
        e = inst.query(":SYSTem:ERRor?").strip()
        out.append(e)
        if e.split(",")[0].strip() == "0":
            break
    return out


def configure(inst, threshold: float, timebase: float) -> None:
    inst.write("*RST")
    inst.query("*OPC?")
    inst.write(":LA:STATe ON")
    inst.write(":LA:POD1:DISPlay ON")
    inst.write(":LA:POD2:DISPlay ON")
    inst.write(f":LA:POD1:THReshold {threshold}")
    inst.write(f":LA:POD2:THReshold {threshold}")
    inst.write(":CHANnel1:DISPlay ON")
    inst.write(":CHANnel1:SCALe 1")
    inst.write(":CHANnel1:OFFSet 0")
    inst.write(f":TIMebase:MAIN:SCALe {timebase}")
    inst.write(":TIMebase:MAIN:OFFSet 0")
    inst.query("*OPC?")


def sync_amplitude(inst) -> float:
    """Peak voltage on CH1 (the SYNC line) over the current acquisition."""
    try:
        return float(inst.query(":MEASure:ITEM? VMAX,CHANnel1").strip())
    except ValueError:
        return float("nan")


def capture(inst, *, use_sync: bool, dwell: float) -> str:
    """Acquire one screenful; return the trigger mode actually used."""
    if use_sync:
        inst.write(":TRIGger:MODE EDGE")
        inst.write(":TRIGger:EDGE:SOURce CHANnel1")
        inst.write(":TRIGger:EDGE:SLOPe POSitive")
        inst.write(":TRIGger:EDGE:LEVel 1.6")
        inst.write(":TRIGger:SWEep NORMal")
        inst.query("*OPC?")
        inst.write(":SINGle")
        for _ in range(40):
            if inst.query(":TRIGger:STATus?").strip() == "STOP":
                return "CH1-edge (phase-locked)"
            time.sleep(0.25)
        # Did not trigger -- fall through to free-run.
    inst.write(":TRIGger:SWEep AUTO")
    inst.query("*OPC?")
    inst.write(":RUN")
    time.sleep(dwell)
    inst.write(":STOP")
    inst.query("*OPC?")
    return "AUTO sweep (software-aligned)"


def read_channels(inst) -> tuple[list[list[int]], float, float, float]:
    """Read D0-D15 samples plus the timebase preamble (xinc, xorig, xref).

    With a SYNC-triggered capture the trigger marks t=0 = the start of frame 0,
    so the preamble lets us map each sample to an exact PATTERN frame index --
    far more robust than reconstructing frame boundaries from the data.
    """
    inst.write(":WAVeform:MODE NORMal")
    inst.write(":WAVeform:FORMat BYTE")
    inst.write(":WAVeform:SOURce D0")
    pre = [float(x) for x in inst.query(":WAVeform:PREamble?").split(",")]
    xinc, xorig, xref = pre[4], pre[5], pre[6]
    chans: list[list[int]] = []
    for n in range(CHANNEL_COUNT):
        inst.write(f":WAVeform:SOURce D{n}")
        chans.append(inst.query_binary_values(":WAVeform:DATA?", datatype="B", container=list))
    return chans, xinc, xorig, xref


def verify_phase_locked(
    chans: list[list[int]], xinc: float, xorig: float, xref: float, min_run: int = 3,
) -> tuple[int, list[int], list[tuple[int, int, int]]]:
    """Verify the capture against PATTERN by recovering the frame clock.

    The Pi's ``time.sleep`` stimulus is not a rigid clock (Python jitter), so we
    do NOT assume a fixed frame period. Instead we run-length-encode the sample
    stream into plateaus -- every PATTERN frame boundary is a word change (no two
    adjacent PATTERN frames are equal), so each real plateau IS one frame. The
    SYNC trigger (t=0) anchors frame 0; plateaus then map to frames sequentially.
    Plateaus shorter than ``min_run`` samples are edge glitches and dropped.

    Returns (frames_checked, per_channel_mismatch_counts, rows) where rows is a
    list of (frame_index, observed_word, expected_word).
    """
    npts = min(len(c) for c in chans)
    words = [sum(1 << n for n in range(CHANNEL_COUNT) if chans[n][t]) for t in range(npts)]
    plateaus: list[list[int]] = []  # [word, start_sample, length]
    for t, w in enumerate(words):
        if plateaus and plateaus[-1][0] == w:
            plateaus[-1][2] += 1
        else:
            plateaus.append([w, t, 1])
    real = [p for p in plateaus if p[2] >= min_run]

    i0 = round((0.0 - xorig) / xinc + xref)  # sample index of the trigger (frame 0 start)
    start = next((j for j, p in enumerate(real) if p[1] >= i0 - 2), 0)

    bad = [0] * CHANNEL_COUNT
    rows: list[tuple[int, int, int]] = []
    checked = 0
    for k in range(len(PATTERN)):
        j = start + k
        if j >= len(real):
            break
        obs = real[j][0]
        exp = PATTERN[k]
        checked += 1
        for n in range(CHANNEL_COUNT):
            if (obs ^ exp) >> n & 1:
                bad[n] += 1
        rows.append((k, obs, exp))
    return checked, bad, rows


def main() -> int:
    ap = argparse.ArgumentParser(description="MSO5204 digital-stimulus capture/verify")
    ap.add_argument("--resource", default=DEFAULT_RESOURCE)
    ap.add_argument("--threshold", type=float, default=1.4, help="pod threshold V (default 1.4)")
    ap.add_argument("--timebase", type=float, default=0.5, help="s/div (default 0.5 -> 5 s screen)")
    args = ap.parse_args()

    rm = pyvisa.ResourceManager("@py")
    inst = rm.open_resource(args.resource, read_termination="\n",
                            write_termination="\n", timeout=15000)
    try:
        print(f"IDN: {inst.query('*IDN?').strip()}")
        configure(inst, args.threshold, args.timebase)
        errs = drain_errors(inst)
        if errs[:-1]:
            print(f"config errors (non-fatal): {errs[:-1]}")

        # Probe SYNC: CH1 must carry the >Vthr pulse for phase-locked capture.
        inst.write(":TRIGger:SWEep AUTO"); inst.query("*OPC?")
        inst.write(":RUN"); time.sleep(args.timebase * 10 + 1); inst.write(":STOP"); inst.query("*OPC?")
        vmax = sync_amplitude(inst)
        print(f"CH1/SYNC Vmax: {vmax:.3f} V")
        if vmax <= 1.6:
            print("\nERROR: SYNC (CH1) is below the 1.6 V trigger level -- cannot phase-lock.\n"
                  "  Check: CH1 probe at 1x, its center conductor on BCM27, and CH1 ground\n"
                  "  tied to the Pi/pod common ground.")
            return 2

        mode = capture(inst, use_sync=True, dwell=args.timebase * 10 + 1)
        print(f"capture mode: {mode}")
        if not mode.startswith("CH1"):
            print("ERROR: SYNC present but no edge triggered in time -- aborting.")
            return 2

        chans, xinc, xorig, xref = read_channels(inst)
        checked, bad, rows = verify_phase_locked(chans, xinc, xorig, xref)
        print(f"phase-locked; {checked} frames checked @ {xinc * 1000:.1f} ms/sample\n")
        for k, obs, exp in rows:
            diff = obs ^ exp
            tag = "OK" if diff == 0 else "DIFF " + ",".join(
                f"D{n}" for n in range(CHANNEL_COUNT) if diff >> n & 1)
            print(f"  f{k:2d}: obs={obs:#06x} exp={exp:#06x}  {tag}")

        total_bad = sum(bad)
        if total_bad == 0:
            print(f"\nRESULT: PASS -- all {CHANNEL_COUNT} channels correct over "
                  f"{checked} frames. Channel mapping verified.")
            return 0
        print("\nper-channel mismatch counts: "
              + "  ".join(f"D{n}:{bad[n]}" for n in range(CHANNEL_COUNT) if bad[n]))
        print(f"\nRESULT: FAIL -- {total_bad} channel-frame mismatches.")
        return 1
    finally:
        inst.close()


if __name__ == "__main__":
    raise SystemExit(main())
