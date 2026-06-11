# StimpyMCP

An MCP server that drives a Raspberry Pi GPIO **digital-pattern stimulus
generator** over the network. Pun sibling to SkippyMCP (SCPI → Skippy):
**STIM**ulus → Stimpy.

It streams a 16-bit pattern (D0..D15) plus a SYNC marker out of the Pi's GPIO via
**pigpio waves** — DMA paced by the PWM/PCM FIFO, so output is hardware-timed,
jitter-free, CPU-idle, and the whole 17-line word changes simultaneously (no
per-line write skew). A staged buffer **goes live at the next SYNC frame** — a
glitch-free double-buffer swap.

## Tools (MCP)

| Tool | Purpose |
|---|---|
| `get_status` | run state, clock, frame counts, device limits |
| `get_pin_map` | BCM pins for D0..D15 + SYNC |
| `set_pattern` | upload words (1 per frame); SYNC on frame 0; live on next sync by default |
| `set_clock_rate` | set the frame clock (Hz); D0 freq = clock/2 |
| `start` / `stop` | activate (repeat/once) / halt (lines low) |
| `load_counter` | *(gated `--allow-builtin-patterns`)* built-in binary counter |

## Run

```bash
# Dev / CI — in-memory simulator, no Pi, no daemon, drives nothing:
stimpy-mcp --simulate --allow-builtin-patterns

# On the Pi — needs pigpiod running (see Deploy):
stimpy-mcp --bind 192.168.137.74 --config /etc/stimpy-mcp/config.json
```

Bit `i` of each frame word drives channel `D<i>`; `clock_rate_hz` is quantized to
whole microseconds (pigpio's 1 µs granularity) — the achieved rate is reported,
never silently rounded.

## Deploy on a Raspberry Pi (native venv + systemd)

pigpio's wave engine needs `pigpiod` running as root (DMA + `/dev/mem`). The MCP
server itself is just an **unprivileged socket client** of the daemon.

1. Install pigpio + the package (`.deb`, or a venv).
2. Enable the daemon: `sudo systemctl enable --now pigpiod` (started with a 1 µs
   sample tick for max rate — see `packaging/pigpiod.service`; raise `-s` to trade
   resolution for CPU).
3. Drop `config.json` at `/etc/stimpy-mcp/config.json` (mode 0600). **`--config` is
   required** (it carries the deployment-specific `pin_map`; `--simulate` may omit it).
   See `examples/config.example.json` — `pin_map` plus api_key + TLS.
4. `sudo systemctl enable --now stimpy-mcp` (see `packaging/stimpy-mcp.service`).

**Security:** binding a non-loopback address requires `api_key` + TLS — the server
prints a loud warning otherwise. Set `allowed_hosts` to the names clients use.

Docker is provided **only** for `--simulate` (client development); it does not
drive real GPIO (that would need `--privileged` + DMA access).
