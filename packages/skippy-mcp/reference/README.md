# reference/

Reference scripting and data — **not production code.** These are working
artifacts from hardware bring-up and ad-hoc testing: useful, real, and they ran
against real instruments, but they take shortcuts (hardcoded paths, no tests, no
packaging, copy-paste lineage) that wouldn't pass review for the shipped server.
Treat them as **examples to crib from**, not as a supported interface.

## logic-analyzer/

The digital-stimulus rig used to bring up and validate SkippyMCP's `:LA:`
(16-channel logic) capture path. A Raspberry Pi drives a known pattern into the
Rigol MSO5204 logic pod; the scope-side scripts capture it back and check it.

| File | Role |
|---|---|
| `pattern.py` | Shared contract: BCM pin map (D0..D15 + SYNC) + the `PATTERN` test vector. |
| `stimulus.py` | Pi-side generator (libgpiod). Originally the 52-frame vector; later a binary counter with busy-wait pacing. |
| `capture.py` | Scope-side oracle: configures `:LA:`, CH1-SYNC-triggered capture, edge-recovered verify against `PATTERN`. |
| `diag_shot.py` | Loops capture+verify and saves a scope screenshot of a PASS/FAIL frame. |
| `diag_fault.py` | Fault localizer: on a FAIL, double-reads + screenshots to pin the layer (transport vs acquisition vs signal). |
| `dso1.json` | The MSO5204's raw-socket VISA resource (`TCPIP0::dso1::5555::SOCKET`). |

The clean, tested re-implementation of the stimulus side lives in the StimpyMCP
server (separate repo/monorepo); `pattern.py` here is the seed of its shared
`rig_contract`.
