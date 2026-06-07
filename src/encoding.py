"""Runtime encoding helpers for Windows console output."""
from __future__ import annotations

import sys


def configure_utf8_stdio() -> None:
    """Keep diagnostic prints from crashing under Windows GBK consoles."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            # Some test runners replace stdio with StringIO-like objects. In
            # those cases, leave the stream alone instead of changing behavior.
            continue
