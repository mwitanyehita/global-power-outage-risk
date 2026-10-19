"""Project configuration.

Single source of truth for paths and runtime detection so the same
imports work both locally and inside Google Colab.

Usage:
    from src.config import DATA_RAW, DATA_EXTERNAL, ensure_dirs
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------
try:
    import google.colab  # type: ignore  # noqa: F401

    IN_COLAB = True
except ImportError:
    IN_COLAB = False


def _find_project_root(start: Path) -> Path:
    """Walk upwards until requirements.txt marks the project root."""
    for candidate in [start, *start.parents]:
        if (candidate / "requirements.txt").is_file():
            return candidate
    return start


# Override with GRIDRISK_ROOT if needed (e.g. Colab clone location).
PROJECT_ROOT = Path(
    os.environ.get("GRIDRISK_ROOT", _find_project_root(Path.cwd()))
).resolve()

# ---------------------------------------------------------------------------
# Data layout (see reports/data_inventory.md for what goes where)
# ---------------------------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
DATA_RAW = DATA_DIR / "raw"          # verbatim downloads, never edited
DATA_EXTERNAL = DATA_DIR / "external"  # reference data (boundaries, lookups)
DATA_PROCESSED = DATA_DIR / "processed"  # cleaned/derived tables

NOTEBOOKS_DIR = PROJECT_ROOT / "notebooks"
REPORTS_DIR = PROJECT_ROOT / "reports"
SRC_DIR = PROJECT_ROOT / "src"

DATA_DIRS = (DATA_RAW, DATA_EXTERNAL, DATA_PROCESSED)


def ensure_dirs() -> None:
    """Create the data directories if they are missing."""
    for path in DATA_DIRS:
        path.mkdir(parents=True, exist_ok=True)


def runtime_summary() -> str:
    """One-line description of the current runtime."""
    where = "Google Colab" if IN_COLAB else "local"
    return f"{where} | Python {sys.version.split()[0]} | root={PROJECT_ROOT}"
