# StimpyMCP

An MCP server that drives a Raspberry Pi GPIO **digital-pattern stimulus
generator** over the network. Pun sibling to SkippyMCP (SCPI → Skippy):
**STIM**ulus → Stimpy.

It streams a 16-bit pattern (D0..D15) plus a SYNC marker out of the Pi's GPIO via
**lgpio** — the maintained successor to pigpio that talks to the kernel GPIO
character device (`/dev/gpiochip0`), so **no root daemon is required**. A
software pacing thread emits one atomic `group_write` per frame, so the whole
17-line word (16 data + SYNC) changes simultaneously — no per-line write skew. A
staged buffer **goes live at the next SYNC frame**: a glitch-free, software
double-buffer swap (the analogue of pigpio's `WAVE_MODE_REPEAT_SYNC`).

Bit `i` of each frame word drives channel `D<i>`; SYNC pulses on frame 0 by
default so a scope or capture tool can phase-lock to the loop start.

## Requirements

- **A Raspberry Pi 4 or better** (Pi 4 or Pi 5). lgpio uses the kernel GPIO
  chardev, so it works on the Pi 5 / RP1 where pigpio's DMA engine does **not**.
  *Not* supported: pigpio-era DMA tricks, or boards without `/dev/gpiochip0`.
- Linux with a GPIO character device (`/dev/gpiochip0`) and the `gpio` group.
- `lgpio` — install the distro package (`sudo apt install python3-lgpio`) or
  `pip install lgpio`.
- **Python 3.11+**.
- For development/CI on any machine, the in-memory `--simulate` backend needs
  none of the above (it drives nothing).

## Tools (MCP)

| Tool | Purpose |
|---|---|
| `get_status` | run state, clock, frame counts, device limits |
| `get_pin_map` | BCM pins for D0..D15 + SYNC |
| `set_pattern` | upload words (1 per frame); SYNC on frame 0; live on next sync by default |
| `set_clock_rate` | set the frame clock (Hz); D0 freq = clock/2 |
| `start` / `stop` | activate (repeat/once) / halt (lines low) |
| `load_counter` | *(gated `--allow-builtin-patterns`)* built-in binary counter |

`clock_rate_hz` is quantized to whole-microsecond ticks; the **achieved** rate is
reported as `actual_clock_rate_hz`, never silently rounded.

## Run

```bash
# Dev / CI — in-memory simulator, no Pi, no GPIO, drives nothing:
stimpy-mcp --simulate --allow-builtin-patterns

# On the Pi — drives real GPIO via lgpio (no daemon):
stimpy-mcp --bind 0.0.0.0 --config /etc/stimpy-mcp/config.json --allow-builtin-patterns
```

`--config` is **required** off the simulator: it carries the deployment-specific
`pin_map`. See `examples/config.example.json`.

## Install & run as a systemd service

The server is a single unprivileged process — no daemon, it only needs read/write
on `/dev/gpiochip0` (via the `gpio` group). The shipped unit
(`packaging/stimpy-mcp.service`) is hardened (`ProtectSystem=strict`,
`ProtectHome`, `PrivateTmp`, `NoNewPrivileges`) and grants only `CAP_SYS_NICE`
for best-effort real-time pacing.

```bash
# 1. A locked service account, in the gpio group (chardev access).
sudo groupadd --system stimpy
sudo useradd  --system --gid stimpy --no-create-home --shell /usr/sbin/nologin stimpy
sudo usermod  -aG gpio stimpy

# 2. A venv at /opt with the package. --system-site-packages reuses the distro
#    python3-lgpio (no compile); otherwise pip pulls lgpio from PyPI.
sudo python3 -m venv --system-site-packages /opt/stimpy-mcp/venv
sudo /opt/stimpy-mcp/venv/bin/pip install stimpy-mcp        # or: pip install -e packages/stimpy-mcp

# 3. Config at /etc/stimpy-mcp/config.json (mode 0600, owned by the service user).
sudo install -d -o stimpy -g stimpy -m 0750 /etc/stimpy-mcp
sudo cp examples/config.example.json /etc/stimpy-mcp/config.json
sudo chown stimpy:stimpy /etc/stimpy-mcp/config.json
sudo chmod 0600 /etc/stimpy-mcp/config.json
#    Edit it: set a long random api_key + the allowed_hosts clients will use.

# 4. Install + enable the unit.
sudo cp packaging/stimpy-mcp.service /etc/systemd/system/stimpy-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now stimpy-mcp
sudo systemctl status stimpy-mcp
```

> **Note (lgpio + systemd):** lgpio creates `.lgd-nfy*` notify FIFOs in its
> working directory at import. A service's default working directory is `/`
> (not writable), which would crash startup. The shipped unit handles this with
> `StateDirectory=stimpy-mcp` + `WorkingDirectory=/var/lib/stimpy-mcp`.

**Security:** access is gated by a bearer `api_key` and an always-on
DNS-rebinding guard. Set `allowed_hosts` to the host:port names clients use
(exact match or a `host:*` port wildcard — host-part globs are not supported, so
list each name, e.g. `"rigelpi.local:*"`). Binding a non-loopback address without
`api_key` prints a loud warning. TLS is optional (`tls.cert` + `tls.key`); on a
trusted LAN, api_key alone is the common posture.

## Example MCP calls

Transport is **MCP Streamable HTTP** at `/mcp`. After `initialize`, every request
carries the `Mcp-Session-Id` returned in the initialize response headers.
Responses are SSE — the JSON-RPC object is on the `data:` line. Each tool result
returns a JSON document (shown below) in `result.content[0]`.

```bash
URL=http://rigelpi.local:8080/mcp
KEY='your-api-key'
HJSON='-H Content-Type:application/json -H Accept:application/json,text/event-stream'

# initialize — grab the session id from the response headers
curl -sS -L -D /tmp/h $HJSON -H "Authorization: Bearer $KEY" "$URL" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}'
SID=$(awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}' /tmp/h | tr -d '\r')
HSID="-H Mcp-Session-Id:$SID"
curl -sS -L $HJSON $HSID -H "Authorization: Bearer $KEY" "$URL" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'
```

`initialize` →

```json
{ "serverInfo": { "name": "stimpy-mcp", "version": "0.3.4" }, "protocolVersion": "2024-11-05" }
```

**get_pin_map** — the BCM wiring the server drives:

```bash
curl -sS -L $HJSON $HSID -H "Authorization: Bearer $KEY" "$URL" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_pin_map","arguments":{}}}'
```
```json
{
  "data_pins": [4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26],
  "sync_pin": 27,
  "channel_count": 16
}
```

**set_pattern** — drive a 4-frame walking-1 at 1 kHz, live on the next SYNC frame
(`go_live` defaults to true; SYNC auto-asserts on frame 0):

```bash
curl -sS -L $HJSON $HSID -H "Authorization: Bearer $KEY" "$URL" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"set_pattern",
       "arguments":{"frames":[1,2,4,8],"clock_rate_hz":1000}}}'
```
```json
{
  "state": "running",
  "running": true,
  "clock_rate_hz": 1000.0,
  "actual_clock_rate_hz": 1000.0,
  "tick_us": 1000,
  "buffer_frames": 4,
  "frames_emitted": 0,
  "loops_completed": 0,
  "current_frame": 0,
  "limits": { "max_pulses": 100000, "max_cbs": 100000, "min_tick_us": 20, "max_frames_per_wave": 100000 },
  "status": "live"
}
```

**get_status** — poll while it free-runs (frame counts advance; `52`-frame example
loop shown: `19*52 + 23 = 1011`):

```bash
curl -sS -L $HJSON $HSID -H "Authorization: Bearer $KEY" "$URL" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_status","arguments":{}}}'
```
```json
{
  "state": "running", "running": true,
  "clock_rate_hz": 1000.0, "actual_clock_rate_hz": 1000.0, "tick_us": 1000,
  "buffer_frames": 52, "frames_emitted": 1011, "loops_completed": 19, "current_frame": 23,
  "limits": { "max_pulses": 100000, "max_cbs": 100000, "min_tick_us": 20, "max_frames_per_wave": 100000 }
}
```

**set_clock_rate** — request a rate; quantization is reported (e.g. 3 kHz →
333 µs tick → 3003.0 Hz achieved). The new rate swaps in at the next loop
boundary:

```bash
curl -sS -L $HJSON $HSID -H "Authorization: Bearer $KEY" "$URL" \
  -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"set_clock_rate","arguments":{"clock_rate_hz":3000}}}'
```
```json
{
  "state": "running", "running": true,
  "clock_rate_hz": 1000.0, "actual_clock_rate_hz": 1000.0, "tick_us": 1000,
  "requested_clock_rate_hz": 3000.0,
  "new_tick_us": 333,
  "new_actual_clock_rate_hz": 3003.003003003003,
  "buffer_frames": 52, "frames_emitted": 4123, "loops_completed": 79, "current_frame": 15,
  "limits": { "max_pulses": 100000, "max_cbs": 100000, "min_tick_us": 20, "max_frames_per_wave": 100000 }
}
```

(`clock_rate_hz`/`actual_clock_rate_hz`/`tick_us` still show the live rate; the
`new_*` fields describe the requested rate that swaps in at the next loop boundary.)

**stop** — halt and drive all lines low:

```bash
curl -sS -L $HJSON $HSID -H "Authorization: Bearer $KEY" "$URL" \
  -d '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"stop","arguments":{}}}'
```
```json
{
  "state": "stopped", "running": false,
  "clock_rate_hz": 0.0, "actual_clock_rate_hz": 0.0, "tick_us": 0,
  "buffer_frames": 52, "frames_emitted": 5200, "loops_completed": 100, "current_frame": 0,
  "limits": { "max_pulses": 100000, "max_cbs": 100000, "min_tick_us": 20, "max_frames_per_wave": 100000 },
  "status": "stopped"
}
```

**load_counter** *(requires `--allow-builtin-patterns`)* — stage the built-in
binary counter; `bits` channels count, `sync_period` sets the SYNC cadence:

```bash
curl -sS -L $HJSON $HSID -H "Authorization: Bearer $KEY" "$URL" \
  -d '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"load_counter","arguments":{"bits":8,"sync_period":256}}}'
```
```json
{
  "state": "running", "running": true,
  "clock_rate_hz": 1000.0, "actual_clock_rate_hz": 1000.0, "tick_us": 1000,
  "buffer_frames": 256, "frames_emitted": 0, "loops_completed": 0, "current_frame": 0,
  "limits": { "max_pulses": 100000, "max_cbs": 100000, "min_tick_us": 20, "max_frames_per_wave": 100000 },
  "status": "live", "bits": 8
}
```

## Docker

Docker is provided **only** for `--simulate` (client development); it does not
drive real GPIO. Real output needs the host's `/dev/gpiochip0`, so deploy the
native systemd service above rather than a container.
