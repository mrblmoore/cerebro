#!/usr/bin/env python3
"""
Entry point for the packaged setup wizard.

Built windowed and launched by the Windows installer as its final step, so
configuration happens inside the install rather than in a browser afterwards.
Runs standalone too, for anyone who wants to change their answers later.
"""

import sys
from pathlib import Path

# The wizard and the backend are siblings in a source checkout and bundled at
# the top level of the frozen build.
_HERE = Path(__file__).resolve().parent
for candidate in (_HERE.parent / "desktop", _HERE):
    if (candidate / "setup_wizard.py").exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
        break
for candidate in (_HERE.parent / "backend", _HERE):
    if (candidate / "app").is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
        break


if __name__ == "__main__":
    import setup_wizard

    sys.exit(setup_wizard.main())
