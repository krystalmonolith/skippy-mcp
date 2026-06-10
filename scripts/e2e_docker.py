#!/usr/bin/env python3
"""Containerized E2E for SkippyMCP across the 4 security modes, against a live scope.

Runs the skippy-mcp image (one container per mode, ``--network host``) pointed at a
real Rigol oscilloscope, then drives the MCP HTTP transport from the host:

  http             : plain HTTP, no auth
  http-apikey      : HTTP + Bearer API key
  https-tls        : HTTPS/TLS, no auth
  https-tls-apikey : HTTPS/TLS + Bearer API key

For each mode it does the full MCP handshake, checks ``get_identity``, autoscales the
target channel via the raw-SCPI escape hatch, measures FREQ + VPP with the real tools,
pulls a screenshot, and exercises the per-request ``X-Skippy-Timeout-Ms`` header. Auth
modes additionally assert that a request without the bearer is rejected (401).

Requires Docker and the ``httpx`` package (a dev dependency). Build the image first::

    docker build -t skippy-mcp:latest .

Then point it at the instrument (a raw socket is recommended — VXI-11/INSTR can corrupt
reads and leak links over a routed link)::

    python scripts/e2e_docker.py --resource 'TCPIP0::169.254.110.63::5555::SOCKET'

Tolerances default to a 10 kHz / 5 V p-p sine on CH1; override with --freq/--vpp/etc.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent


# -- MCP Streamable-HTTP client -------------------------------------------
def _parse_sse(text: str) -> dict:
    """Extract the JSON-RPC object from a single-message SSE body."""
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError(f"no SSE data in response: {text!r}")


class MCP:
    def __init__(self, base_url: str, api_key: str | None, verify: object, call_timeout_ms: int):
        self.base = base_url
        self.call_timeout_ms = call_timeout_ms
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        # Mount("/mcp") 307-redirects to /mcp/; follow it (307 preserves POST+body).
        self.client = httpx.Client(verify=verify, timeout=30.0, follow_redirects=True)
        self.sid: str | None = None

    def _post(self, payload: dict, extra: dict | None = None) -> httpx.Response:
        h = dict(self.headers)
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        if extra:
            h.update(extra)
        return self.client.post(self.base, content=json.dumps(payload), headers=h)

    def initialize(self) -> dict:
        r = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "e2e", "version": "1"},
                },
            }
        )
        r.raise_for_status()
        self.sid = r.headers.get("mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return _parse_sse(r.text)["result"]

    def call(self, name: str, args: dict, timeout_ms: int | None = -1) -> dict:
        ms = self.call_timeout_ms if timeout_ms == -1 else timeout_ms
        extra = {"X-Skippy-Timeout-Ms": str(ms)} if ms is not None else None
        r = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            },
            extra,
        )
        r.raise_for_status()
        return _parse_sse(r.text)["result"]


# -- docker helpers -------------------------------------------------------
def _sh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def _write_configs(cfg_dir: Path, api_key: str) -> None:
    if cfg_dir.exists():
        shutil.rmtree(cfg_dir)
    cfg_dir.mkdir(parents=True)
    for pem in ("dev-cert.pem", "dev-key.pem"):
        dst = cfg_dir / pem
        shutil.copy(REPO / "examples/json-configuration" / pem, dst)
        dst.chmod(0o644)
    tls = {"cert": "/cfg/dev-cert.pem", "key": "/cfg/dev-key.pem"}
    configs = {
        "http-apikey": {"api_key": api_key},
        "https-tls": {"tls": tls},
        "https-tls-apikey": {"api_key": api_key, "tls": tls},
    }
    for name, body in configs.items():
        p = cfg_dir / f"{name}.json"
        p.write_text(json.dumps(body))
        p.chmod(0o644)
    cfg_dir.chmod(0o755)


def _start(opts: argparse.Namespace, cfg_dir: Path, mode: dict) -> str:
    name = f"skippy-e2e-{mode['name']}"
    _sh("docker", "rm", "-f", name, check=False)
    cmd = [
        "docker", "run", "-d", "--rm", "--network", "host", "--name", name,
        "-v", f"{cfg_dir}:/cfg:ro", opts.image,
        "--resource", opts.resource, "--bind", "127.0.0.1", "--port", str(opts.port),
        "--allow-raw-scpi",
    ]
    if mode["tls"] or mode["auth"]:
        cmd += ["--config", f"/cfg/{mode['name']}.json"]
    _sh(*cmd)
    return name


def _wait_ready(opts: argparse.Namespace, cfg_dir: Path, mode: dict, name: str) -> MCP:
    scheme = "https" if mode["tls"] else "http"
    base = f"{scheme}://127.0.0.1:{opts.port}/mcp"
    verify: object = str(cfg_dir / "dev-cert.pem") if mode["tls"] else True
    api_key = opts.api_key if mode["auth"] else None
    deadline = time.time() + opts.ready_timeout
    last = ""
    while time.time() < deadline:
        running = _sh("docker", "inspect", "-f", "{{.State.Running}}", name, check=False)
        if running.stdout.strip() != "true":
            logs = _sh("docker", "logs", name, check=False)
            raise RuntimeError(f"container exited early:\n{logs.stdout}\n{logs.stderr}")
        try:
            mcp = MCP(base, api_key, verify, opts.call_timeout_ms)
            mcp.initialize()
            return mcp
        except Exception as exc:  # not ready yet
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)
    raise RuntimeError(f"server not ready in {opts.ready_timeout}s (last: {last})")


# -- test sequence --------------------------------------------------------
def _measure_retry(mcp: MCP, opts: argparse.Namespace, mtype: str, retries: int = 12) -> float:
    """Measure mtype on the target channel, retrying while it reports unavailable."""
    last = ""
    for _ in range(retries):
        res = mcp.call("measure", {"type": mtype, "source": opts.channel})
        if not res.get("isError"):
            return float(res["structuredContent"]["value"])
        last = res["content"][0]["text"]
        time.sleep(0.8)
    raise AssertionError(f"measure {mtype} never became available: {last}")


def _run_sequence(opts: argparse.Namespace, mode: dict, mcp: MCP) -> list[str]:
    checks: list[str] = []

    idn = mcp.call("get_identity", {})
    model = idn["structuredContent"]["model"]
    checks.append(f"get_identity -> {model}")

    # Raw-SCPI escape hatch: autoscale to display the unknown-offset signal.
    auto = mcp.call("scpi_raw", {"command": ":AUToscale", "expect_response": False})
    assert not auto.get("isError"), auto
    time.sleep(opts.autoscale_settle)
    mcp.call("capture", {"action": "run"})
    time.sleep(1.0)

    freq = _measure_retry(mcp, opts, "freq")
    assert abs(freq - opts.freq) <= opts.freq_tol, (
        f"freq {freq} Hz off {opts.freq}+-{opts.freq_tol}"
    )
    checks.append(f"measure freq -> {freq:.1f} Hz")

    vpp = _measure_retry(mcp, opts, "vpp")
    assert abs(vpp - opts.vpp) <= opts.vpp_tol, f"vpp {vpp} V off {opts.vpp}+-{opts.vpp_tol}"
    checks.append(f"measure vpp -> {vpp:.3f} V")

    shot = mcp.call("screenshot", {})
    img = shot["content"][0]
    assert img["type"] == "image" and img["mimeType"] == "image/png", img
    png = base64.b64decode(img["data"])
    assert png.startswith(b"\x89PNG"), "screenshot is not PNG"
    checks.append(f"screenshot -> {len(png)} B PNG")

    mcp.call("get_identity", {}, timeout_ms=8000)  # per-call timeout header on a live call
    checks.append("timeout-header call ok")
    return checks


def _auth_negative(opts: argparse.Namespace, cfg_dir: Path, mode: dict) -> str:
    """Without a bearer, an auth-mode server must reject with 401."""
    scheme = "https" if mode["tls"] else "http"
    base = f"{scheme}://127.0.0.1:{opts.port}/mcp"
    verify: object = str(cfg_dir / "dev-cert.pem") if mode["tls"] else True
    r = httpx.post(
        base,
        content='{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2024-11-05","capabilities":{},'
        '"clientInfo":{"name":"x","version":"1"}}}',
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        verify=verify,
        timeout=10.0,
        follow_redirects=True,
    )
    assert r.status_code == 401, f"expected 401 without bearer, got {r.status_code}"
    return "no-bearer -> 401"


MODES = [
    {"name": "http", "tls": False, "auth": False},
    {"name": "http-apikey", "tls": False, "auth": True},
    {"name": "https-tls", "tls": True, "auth": False},
    {"name": "https-tls-apikey", "tls": True, "auth": True},
]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--resource",
        default=os.environ.get("SKIPPY_E2E_RESOURCE"),
        help="VISA resource for the scope (e.g. TCPIP0::<ip>::5555::SOCKET). "
        "Env: SKIPPY_E2E_RESOURCE. A raw socket is recommended over INSTR/VXI-11.",
    )
    p.add_argument("--image", default="skippy-mcp:latest", help="Container image tag.")
    p.add_argument("--port", type=int, default=8080, help="Host port to serve on.")
    p.add_argument("--channel", default="CH1", help="Channel carrying the signal.")
    p.add_argument("--freq", type=float, default=10000.0, help="Expected frequency (Hz).")
    p.add_argument("--freq-tol", type=float, default=1000.0, help="Frequency tolerance (Hz).")
    p.add_argument("--vpp", type=float, default=5.0, help="Expected peak-to-peak (V).")
    p.add_argument("--vpp-tol", type=float, default=1.0, help="Vpp tolerance (V).")
    p.add_argument(
        "--call-timeout-ms", type=int, default=15000, help="Per-call X-Skippy-Timeout-Ms."
    )
    p.add_argument(
        "--autoscale-settle", type=float, default=4.0, help="Seconds to wait after :AUToscale."
    )
    p.add_argument(
        "--ready-timeout", type=float, default=45.0, help="Startup readiness timeout (s)."
    )
    opts = p.parse_args()
    if not opts.resource:
        p.error("--resource (or SKIPPY_E2E_RESOURCE) is required")
    opts.api_key = "e2e-" + secrets.token_hex(16)
    return opts


def main() -> int:
    opts = _parse_args()
    cfg_dir = Path(tempfile.gettempdir()) / "skippy-e2e-cfg"
    _write_configs(cfg_dir, opts.api_key)

    results: list[tuple[str, bool, list[str], str]] = []
    for mode in MODES:
        name = _start(opts, cfg_dir, mode)
        checks: list[str] = []
        ok, err = True, ""
        try:
            mcp = _wait_ready(opts, cfg_dir, mode, name)
            banner = _sh("docker", "logs", name, check=False).stdout
            checks = _run_sequence(opts, mode, mcp)
            if mode["auth"]:
                checks.append(_auth_negative(opts, cfg_dir, mode))
            for line in banner.splitlines():
                s = line.strip()
                if s.startswith(("TLS", "API key", "WARNING", "Endpoint")):
                    checks.append(f"banner: {s}")
        except Exception as exc:
            ok, err = False, f"{type(exc).__name__}: {exc}"
        finally:
            _sh("docker", "stop", "-t", "2", name, check=False)
        results.append((mode["name"], ok, checks, err))
        time.sleep(1.0)

    print("\n" + "=" * 68)
    print("SkippyMCP containerized E2E — 4 security modes (live instrument)")
    print("=" * 68)
    all_ok = True
    for name, ok, checks, err in results:
        print(f"\n[{'PASS' if ok else 'FAIL'}] {name}")
        for c in checks:
            print(f"    - {c}")
        if not ok:
            all_ok = False
            print(f"    ! {err}")
    print("\n" + ("ALL MODES PASSED" if all_ok else "SOME MODES FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
