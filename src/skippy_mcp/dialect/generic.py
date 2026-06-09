"""Placeholder module — intentionally provides no dialect.

Per the design (decision Q2), an instrument whose model matches no registered
dialect is refused with :class:`UnsupportedInstrumentError` rather than driven by
guessed commands. A conservative analog-only generic dialect could live here in
future if that policy is revisited; today it is deliberately absent.
"""

from __future__ import annotations
