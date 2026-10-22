"""Copy collector ciphertext into a dedicated read-only web data source."""
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

os.umask(0o077)
COLLECTOR = Path('/opt/jego-research')
TARGET = Path('/opt/jego-sms-source')


def synchronize():
    if not (COLLECTOR / 'research-sms.sqlite3').is_file():
        raise FileNotFoundError('Collector database unavailable')
    if hashlib.sha256((COLLECTOR / 'sms-storage.key').read_bytes()).digest() != hashlib.sha256((TARGET / 'sms-storage.key').read_bytes()).digest():
        raise ValueError('Source encryption key changed')
    source = sqlite3.connect((COLLECTOR / 'research-sms.sqlite3').resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    dest = sqlite3.connect(TARGET / 'research-sms.sqlite3', timeout=5)
    try:
        source.execute('PRAGMA query_only=ON')
        cursor = dest.execute('SELECT COALESCE(MAX(id),0) FROM received').fetchone()[0]
        rows = source.execute('SELECT id,fingerprint,encrypted_record,ingested_at FROM received WHERE id>? ORDER BY id LIMIT 500', (cursor,)).fetchall()
        dest.executemany('INSERT OR IGNORE INTO received(id,fingerprint,encrypted_record,ingested_at) VALUES(?,?,?,?)', rows)
        dest.commit()
        return len(rows)
    finally:
        source.close()
        dest.close()


def main():
    TARGET.mkdir(mode=0o700, exist_ok=True)
    while True:
        try:
            imported = synchronize()
            status = {'state': 'connected', 'heartbeat': int(time.time()), 'imported': imported}
        except (sqlite3.Error, OSError, ValueError) as exc:
            status = {'state': 'error', 'heartbeat': int(time.time()), 'error_type': type(exc).__name__}
        temporary = TARGET / 'bridge-status.tmp'
        temporary.write_text(json.dumps(status))
        temporary.replace(TARGET / 'bridge-status.json')
        time.sleep(5)


if __name__ == '__main__':
    main()
