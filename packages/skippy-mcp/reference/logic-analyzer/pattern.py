"""Compatibility shim — the canonical contract now lives in ``rig_contract``.

The pin map and the ``PATTERN`` test vector used to be defined here. In the
skippy-mcp monorepo they are the shared ``rig_contract`` package (the rig "IDL"),
so there is exactly one source of truth. These reference scripts predate the
split and import ``pattern``; this thin re-export keeps them runnable without
duplicating the data.

Run them from the monorepo venv with ``rig_contract`` installed:
``pip install -e packages/rig-contract``.
"""

from rig_contract import (  # noqa: F401  (re-export)
    CHANNEL_COUNT,
    DATA_PINS,
    MASK,
    PATTERN,
    SYNC_FRAME,
    SYNC_PIN,
    build_pattern,
    counter_frames,
    counter_sync_frames,
)
