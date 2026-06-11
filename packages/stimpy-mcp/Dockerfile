# StimpyMCP container — SIMULATE MODE ONLY (client dev / CI demo).
# It does NOT drive real GPIO: pigpio needs DMA/root, which would require a
# --privileged container and defeat containment. Deploy to a Pi via venv+systemd.
#
# Build context must be the monorepo root (rig-contract is a sibling path dep):
#   docker build -f packages/stimpy-mcp/Dockerfile -t stimpy-mcp .

FROM python:3.12-slim AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY packages/rig-contract ./rig-contract
COPY packages/stimpy-mcp ./stimpy-mcp
RUN pip install ./rig-contract && pip install ./stimpy-mcp

FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="StimpyMCP" \
      org.opencontainers.image.description="GPIO digital-stimulus MCP server (simulate-only image)"
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=builder /opt/venv /opt/venv
RUN useradd --create-home --uid 10001 stimpy
USER stimpy
EXPOSE 8080
ENTRYPOINT ["stimpy-mcp", "--simulate", "--bind", "0.0.0.0"]
