"""Shared idempotent-download helper for the fetch_*.py scripts."""
from pathlib import Path

import requests


def fetch_if_missing(url: str, out_path: Path, force: bool = False) -> bool:
    """Download url to out_path unless it already exists. Returns True if a
    download happened, False if skipped."""
    if out_path.exists() and not force:
        print(f"{out_path} already exists, skipping (use --force to refetch)")
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    print(f"Fetched {url} -> {out_path} ({len(resp.content)} bytes)")
    return True
