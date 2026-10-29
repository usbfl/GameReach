"""Create a consistent SQLite backup without overwriting an existing file."""

import argparse, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings

p = argparse.ArgumentParser()
p.add_argument("--output", required=True, type=Path)
args = p.parse_args()
url = Settings().resolved_db_url()
if not url.startswith("sqlite:///"):
    raise SystemExit("This helper supports the existing SQLite deployment only.")
source = Path(url.removeprefix("sqlite:///")).resolve()
destination = args.output.resolve()
if not source.exists():
    raise SystemExit("Database not found; no file created.")
if destination.exists() or source == destination:
    raise SystemExit("Backup destination already exists; choose a new filename.")
destination.parent.mkdir(parents=True, exist_ok=True)
with (
    sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src,
    sqlite3.connect(destination) as dst,
):
    src.backup(dst)
    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Backup integrity check failed.")
print("Backup created:", destination)
