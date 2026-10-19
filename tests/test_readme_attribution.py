"""Guards for the README's data-source attribution requirements.

The project requires that every redistributed dataset carries its license
and attribution string in the README (project principle: "Document data
sources, licenses, and geographic coverage"). These tests fail loudly if a
later edit drops an attribution line, so a documentation prune cannot
silently break licensing compliance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"

# Attribution fragments that must survive any README edit.
REQUIRED_ATTRIBUTION = {
    "geoBoundaries package license": "CC BY 4.0",
    "geoBoundaries attribution notice": "geoBoundaries",
    "geoBoundaries copyright holder": "© William & Mary",
    "Kenya ADM0/ADM1 license": "Public Domain",
    "Kenya ADM2 license": "Creative Commons Attribution 3.0 Intergovernmental Organisations",
    "Nigeria ADM2 license": "Creative Commons Attribution 4.0 International",
    "Open-Meteo credit": "Weather data by Open-Meteo",
    "Open-Meteo reanalysis credit": "ECMWF ERA5/ERA5-Land",
}


@pytest.fixture(scope="module")
def readme_text() -> str:
    assert README.exists(), "README.md is missing"
    return README.read_text(encoding="utf-8")


@pytest.mark.parametrize("label,fragment", sorted(REQUIRED_ATTRIBUTION.items()))
def test_readme_keeps_required_attribution(readme_text, label, fragment):
    assert fragment in readme_text, f"README lost the {label} attribution: {fragment!r}"


def test_readme_documents_the_three_pilot_countries(readme_text):
    for country in ("Kenya", "Nigeria", "South Africa"):
        assert country in readme_text, f"README does not mention {country}"


def test_readme_states_how_to_run_the_tests(readme_text):
    assert "pytest" in readme_text, "README must document how to run the test suite"


def test_readme_distinguishes_code_license_from_data_licenses(readme_text):
    assert "MIT" in readme_text, "README must state the code license"
