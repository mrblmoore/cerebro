#!/usr/bin/env python3
"""
Entry point for the packaged Cerebro widget executable.

Built windowed, so double-clicking it shows the widget and no console.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for candidate in (_HERE.parent / "desktop", _HERE):
    if (candidate / "widget.py").exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

if __name__ == "__main__":
    import widget

    sys.exit(widget.main())
