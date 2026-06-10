# SkippyMCP — MCP server for Rigol oscilloscopes.
#
# Pure-Python (pyvisa-py backend), so no vendor VISA libraries are needed.
# Multi-stage: build into a venv, then copy only the venv into a slim runtime.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
# Copy only what the package build needs (see .dockerignore for exclusions).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .

# --- runtime ---------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="SkippyMCP" \
      org.opencontainers.image.description="MCP server for controlling Rigol oscilloscopes via SCPI/PyVISA." \
      org.opencontainers.image.source="https://github.com/krystalmonolith/skippy-mcp" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv

# Run unprivileged.
RUN useradd --create-home --uid 10001 skippy
USER skippy

# The server speaks MCP over HTTP (Streamable HTTP) on port 8080; it binds
# loopback by default — pass --bind 0.0.0.0 to expose it outside the container.
# Example: docker run --rm --network host skippy-mcp:latest \
#            --resource TCPIP0::scope::5555::SOCKET --bind 0.0.0.0
EXPOSE 8080
ENTRYPOINT ["skippy-mcp"]
