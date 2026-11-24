"""
Reads the compact OIS-implied expected-funds-rate history the dashboard's
Rate Expectations tab uses, from ../ois_derived/history.json.

That file is produced by 09_export_dtcc_ois.py from DTCC's public swap-trade
tape (see 06/07/08) and is gitignored: like cme_derived/, it is rebuilt in the
workflow and ships only inside the deployed page. It is optional -- without it
the tab shows the CME charts alone.
"""
import json
from pathlib import Path

OIS_HISTORY_PATH = Path(__file__).parent.parent / "ois_derived" / "history.json"


def load_history() -> dict | None:
    if not OIS_HISTORY_PATH.exists():
        return None
    return json.loads(OIS_HISTORY_PATH.read_text())
