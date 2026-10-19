"""Project configuration.

Single source of truth for paths, pilot constants, and runtime detection so
the same imports work both locally (from any working directory) and inside
Google Colab.

Environment overrides:
    GRIDRISK_ROOT   project root (e.g. the Colab clone location)
    GRIDRISK_SEED   integer random seed for models (default 42)

Usage:
    from src.config import DATA_RAW, DATA_EXTERNAL, PILOT_COUNTRIES, SEED, ensure_dirs
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final, Literal, TypeAlias

# ---------------------------------------------------------------------------
# Typed constants for the pilot geography
# ---------------------------------------------------------------------------

CountryISO3: TypeAlias = Literal["KEN", "NGA", "ZAF"]
AdmLevel: TypeAlias = Literal["ADM0", "ADM1", "ADM2"]

# Order matches src.ingestion.boundaries.PILOT_COUNTRIES (cross-checked by tests).
PILOT_COUNTRIES: Final[tuple[CountryISO3, ...]] = ("KEN", "ZAF", "NGA")
ADM_LEVELS: Final[tuple[AdmLevel, ...]] = ("ADM0", "ADM1", "ADM2")

COUNTRY_NAMES: Final[dict[str, str]] = {
    "KEN": "Kenya",
    "NGA": "Nigeria",
    "ZAF": "South Africa",
}

# IANA zones for local-time feature engineering (none of the pilots observe DST,
# but the offset differs per country, so local hour must be derived per country).
COUNTRY_TIMEZONES: Final[dict[str, str]] = {
    "KEN": "Africa/Nairobi",
    "NGA": "Africa/Lagos",
    "ZAF": "Africa/Johannesburg",
}

# ---------------------------------------------------------------------------
# Runtime detection
# ---------------------------------------------------------------------------
try:
    import google.colab  # type: ignore  # noqa: F401

    IN_COLAB = True
except ImportError:
    IN_COLAB = False


def _find_project_root(start: Path) -> Path | None:
    """Walk upwards until requirements.txt + src/ mark the project root.

    Returns None when no marker is found, so callers can fall back or fail
    loudly instead of silently operating in the wrong directory.
    """
    for candidate in (start, *start.parents):
        if (candidate / "requirements.txt").is_file() and (candidate / "src").is_dir():
            return candidate
    return None


def _resolve_project_root(
    env_root: str | None, cwd: Path, module_file: Path
) -> Path:
    """Locate the project root.

    Resolution order:
      1. ``GRIDRISK_ROOT`` (validated: must exist and contain requirements.txt)
      2. walk upwards from this file — correct from any working directory
      3. walk upwards from the current working directory

    Raises RuntimeError when the root cannot be found, rather than silently
    returning a directory that does not hold the project data layout.
    """
    if env_root:
        root = Path(env_root).expanduser().resolve()
        if not root.is_dir():
            raise RuntimeError(f"GRIDRISK_ROOT does not exist: {root}")
        if not (root / "requirements.txt").is_file():
            raise RuntimeError(
                f"GRIDRISK_ROOT is not a project root (no requirements.txt): {root}"
            )
        return root

    root = _find_project_root(module_file.parent) or _find_project_root(cwd)
    if root is None:
        raise RuntimeError(
            "Could not locate the project root (no requirements.txt above "
            f"{module_file.parent} or {cwd}); set GRIDRISK_ROOT."
        )
    return root


PROJECT_ROOT: Final[Path] = _resolve_project_root(
    os.environ.get("GRIDRISK_ROOT"), Path.cwd(), Path(__file__)
)

# ---------------------------------------------------------------------------
# Seed handling (models, CV folds, calibrators)
# ---------------------------------------------------------------------------
DEFAULT_SEED: Final[int] = 42


def _seed_from_env(default: int = DEFAULT_SEED) -> int:
    """Read GRIDRISK_SEED as a non-negative integer, else return default."""
    raw = os.environ.get("GRIDRISK_SEED")
    if raw is None or not raw.strip():
        return default
    try:
        seed = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"GRIDRISK_SEED must be an integer, got {raw!r}") from exc
    if seed < 0:
        raise RuntimeError(f"GRIDRISK_SEED must be non-negative, got {seed}")
    return seed


SEED: Final[int] = _seed_from_env()

# ---------------------------------------------------------------------------
# Data layout (see reports/data_inventory.md for what goes where)
# ---------------------------------------------------------------------------
DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
DATA_RAW: Final[Path] = DATA_DIR / "raw"           # verbatim downloads, never edited
DATA_EXTERNAL: Final[Path] = DATA_DIR / "external"  # reference data (boundaries, lookups)
DATA_PROCESSED: Final[Path] = DATA_DIR / "processed"  # cleaned/derived tables
# Sub-folders of the data zones. Defined here so that every module resolves
# a path through one source of truth instead of re-deriving it locally.
WEATHER_DIR: Final[Path] = DATA_RAW / "weather"     # Open-Meteo archive responses
OUTAGES_DIR: Final[Path] = DATA_RAW / "outages"     # outage records and notices
WORLDBANK_DIR: Final[Path] = DATA_RAW / "worldbank"  # World Bank indicator series
BOUNDARIES_DIR: Final[Path] = DATA_EXTERNAL / "boundaries"  # geoBoundaries files

NOTEBOOKS_DIR: Final[Path] = PROJECT_ROOT / "notebooks"
REPORTS_DIR: Final[Path] = PROJECT_ROOT / "reports"
SRC_DIR: Final[Path] = PROJECT_ROOT / "src"

DATA_DIRS: Final[tuple[Path, ...]] = (DATA_RAW, DATA_EXTERNAL, DATA_PROCESSED)


def ensure_dirs() -> None:
    """Create the data directories if they are missing."""
    for path in DATA_DIRS:
        path.mkdir(parents=True, exist_ok=True)


def runtime_summary() -> str:
    """One-line description of the current runtime."""
    where = "Google Colab" if IN_COLAB else "local"
    return (
        f"{where} | Python {sys.version.split()[0]} | seed={SEED} | root={PROJECT_ROOT}"
    )
