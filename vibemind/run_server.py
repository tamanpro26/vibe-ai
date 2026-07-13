"""
vibemind/run_server.py
Standalone entry point for PyInstaller -- freezes vibemind.server:app (and
everything it transitively imports from models/, core/, tools/) into one
executable that runs without a system Python install. electron/main.cjs
spawns THIS exe in a packaged build; in development it still spawns
`python -m uvicorn vibemind.server:app` directly against the live source.
"""
from __future__ import annotations

import os
import sys

# When frozen by PyInstaller, the bundle's temp extraction dir needs to be on
# sys.path for `import vibemind` / `import models` / `import core` / `import
# tools` (this repo's flat top-level package layout) to resolve.
if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)  # type: ignore[attr-defined]

import uvicorn

from vibemind.server import app

if __name__ == "__main__":
    host = os.getenv("VIBEMIND_API_HOST", "127.0.0.1")
    port = int(os.getenv("VIBEMIND_API_PORT", "8756"))
    uvicorn.run(app, host=host, port=port, log_level="info")
