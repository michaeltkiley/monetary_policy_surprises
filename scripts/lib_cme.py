"""
Reads the small, non-proprietary slice of CME data this dashboard needs --
the ZQ-implied Fed Funds rate path and bucketed SR3 risk-neutral
probability distributions -- from ../cme_derived/latest.json.

That file is produced and kept up to date by a separate, private repo
(cme-rate-data) which holds the actual CME STLINT settlement files, the
parsed options/futures CSVs, and probs.duckdb -- none of which are public.
Its own export step (04_export_dashboard_json.py there) runs the same
Carlson-Craig-Melick math and contract-selection logic this module used to
run directly against a local checkout of ../../cme_tests, and pushes just
this JSON here on each update. See that repo's README for the full chain
(Gmail -> Drive -> GitHub -> this file).
"""
import json
from pathlib import Path

CME_DERIVED_PATH = Path(__file__).parent.parent / "cme_derived" / "latest.json"


def load() -> dict:
    if not CME_DERIVED_PATH.exists():
        raise FileNotFoundError(
            f"{CME_DERIVED_PATH} not found -- expected to be pushed here by the "
            "cme-rate-data private repo's export workflow."
        )
    return json.loads(CME_DERIVED_PATH.read_text())
