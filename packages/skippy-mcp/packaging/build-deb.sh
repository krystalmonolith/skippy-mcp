#!/usr/bin/env bash
# Build a Debian package for SkippyMCP.
#
# Strategy: ship our pure-Python wheel inside the .deb; the postinst builds a
# venv with the TARGET machine's python3 and pip-installs it (pulling pyvisa /
# pyvisa-py / pillow / mcp from PyPI). Building the venv on the target avoids
# venv-relocation/shebang breakage and Python-version coupling. Architecture is
# therefore "all" (our wheel is py3-none-any).
#
# Usage: packaging/build-deb.sh   ->   build/deb/skippy-mcp_<version>_all.deb
set -euo pipefail

here="$(dirname "$(readlink -f "$0")")"
root="$(dirname "$here")"
# Interpreter used only to BUILD our pure-Python wheel (must have pip). The
# target machine's python3 builds the runtime venv via the postinst.
python_bin="${PYTHON:-python3}"
version="$(grep -m1 '^version = ' "$root/pyproject.toml" | sed -E 's/.*"([^"]+)".*/\1/')"
pkg="skippy-mcp"
build="$root/build/deb"
stage="$build/${pkg}_${version}_all"

echo "Building ${pkg} ${version} ..."
rm -rf "$build"
mkdir -p "$stage/DEBIAN" "$stage/opt/skippy-mcp/dist" "$stage/usr/bin"

# Our wheel only (deps resolved at install time on the target).
"$python_bin" -m pip wheel --no-deps "$root" -w "$stage/opt/skippy-mcp/dist" >/dev/null

cat > "$stage/DEBIAN/control" <<EOF
Package: ${pkg}
Version: ${version}
Section: electronics
Priority: optional
Architecture: all
Depends: python3 (>= 3.11), python3-venv, python3-pip
Maintainer: Mark Deazley <1504009+krystalmonolith@users.noreply.github.com>
Homepage: https://github.com/krystalmonolith/skippy-mcp
Description: MCP server for controlling Rigol oscilloscopes via SCPI/PyVISA.
 SkippyMCP exposes Rigol oscilloscope control (channels, triggers, capture,
 measurements, screenshots, waveform data, bus decode) as Model Context
 Protocol tools, so an AI assistant can drive a scope over SCPI.
EOF

cat > "$stage/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
VENV=/opt/skippy-mcp/venv
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet /opt/skippy-mcp/dist/*.whl
EOF

cat > "$stage/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    rm -rf /opt/skippy-mcp/venv
fi
EOF

cat > "$stage/usr/bin/skippy-mcp" <<'EOF'
#!/bin/sh
exec /opt/skippy-mcp/venv/bin/skippy-mcp "$@"
EOF

chmod 0755 "$stage/DEBIAN/postinst" "$stage/DEBIAN/postrm" "$stage/usr/bin/skippy-mcp"
# Normalize dir perms to 0755. Note: on a setgid parent, numeric chmod does not
# clear the inherited setgid bit, so strip it symbolically (dpkg-deb rejects it).
find "$stage" -type d -exec chmod 0755 {} +
find "$stage" -type d -exec chmod g-s {} +

deb="$build/${pkg}_${version}_all.deb"
dpkg-deb --root-owner-group --build "$stage" "$deb" >/dev/null
echo "Built: $deb"
dpkg-deb --info "$deb" | sed 's/^/  /'
