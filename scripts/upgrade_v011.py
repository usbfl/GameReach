#!/usr/bin/env python3
"""Non-destructive SQLite upgrade inspection and backup/restore to a NEW path.

v0.11 adds JSON fields and record namespaces, not SQL tables/columns.
Run with services stopped. No data or confirmed card is rewritten.
"""
import argparse
import json
import sqlite3
from pathlib import Path


def copy_database(source, destination):
    src, dst = Path(source).resolve(), Path(destination).resolve()
    if not src.is_file():
        raise ValueError('Source database does not exist')
    if dst.exists() or src == dst:
        raise ValueError('Destination must be a new path; existing data is never overwritten')
    dst.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(src.as_uri() + '?mode=ro', uri=True) as old:
        if old.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Source integrity check failed')
        with sqlite3.connect(dst) as new:
            old.backup(new)
            if new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Destination integrity check failed')
            tables = [r[0] for r in new.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            counts = {name: new.execute('SELECT COUNT(*) FROM "'+name.replace('"', '""')+'"').fetchone()[0] for name in tables}
    return {'source': str(src), 'destination': str(dst), 'integrity': 'ok', 'table_counts': counts,
            'schema_migration': 'none; additive JSON fields only', 'rewritten_user_records': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--output', required=True, help='New backup or restored database path')
    p.add_argument('--restore', action='store_true', help='Label this copy as rollback restoration')
    args = p.parse_args()
    result = copy_database(args.source, args.output)
    print(json.dumps(result | {'operation': 'restore' if args.restore else 'backup_for_upgrade'}, indent=2))


if __name__ == '__main__':
    main()
