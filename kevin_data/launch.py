"""
Enhanced CLI launcher — applies HK/China data patches then starts tradingagents.

Called by run.bat / run.sh instead of the bare `tradingagents` entry-point.
The USE_DATA_ENHANCER env var gates whether the patches are applied; when not
set (or set to 0) behaviour is identical to running `tradingagents` directly.
"""

import os
import sys

# Add project root to path so both `tradingagents` and `kevin_data` are importable
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

if os.environ.get("USE_DATA_ENHANCER", "0") == "1":
    try:
        from kevin_data.data_enhancer import apply_patches
        apply_patches()
    except Exception as exc:
        import logging
        logging.warning("DataEnhancer could not be applied: %s", exc)

# Hand off to the standard tradingagents CLI entry-point
from cli.main import app

if __name__ == "__main__":
    app()
