"""
Production, stage 4: inject the JSON payload (03) into the HTML template,
producing a self-contained static dashboard. Open directly in a browser --
no server needed.

Usage:
    python3 04_build_dashboard.py [--run-date YYYYMMDD]
"""
import argparse
import glob
from datetime import date
from pathlib import Path


def latest(out_dir: Path, pattern: str) -> Path:
    matches = sorted(glob.glob(str(out_dir / pattern)))
    if not matches:
        raise FileNotFoundError(f"No {pattern} in {out_dir}")
    return Path(matches[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-date", default=date.today().strftime("%Y%m%d"))
    ap.add_argument("--out-dir", default="../output")
    ap.add_argument("--template", default="dashboard_template.html")
    ap.add_argument("--pages-out", default="../docs/index.html",
                     help="also write a stable copy here for GitHub Pages (fixed URL, not date-stamped)")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)

    data_path = out_dir / f"{args.run_date}_dashboard_data.json"
    if not data_path.exists():
        data_path = latest(out_dir, "*_dashboard_data.json")
    print(f"Reading {data_path}")
    payload_json = data_path.read_text()

    template = Path(args.template).read_text()
    if "__DATA_JSON__" not in template:
        raise ValueError(f"{args.template} is missing the __DATA_JSON__ placeholder")
    html = template.replace("__DATA_JSON__", payload_json)

    out_path = out_dir / f"{args.run_date}_dashboard.html"
    out_path.write_text(html)
    print(f"Wrote {out_path} ({len(html)} bytes)")

    if args.pages_out:
        pages_path = Path(args.pages_out)
        pages_path.parent.mkdir(parents=True, exist_ok=True)
        pages_path.write_text(html)
        print(f"Wrote {pages_path} (stable Pages copy)")


if __name__ == "__main__":
    main()
