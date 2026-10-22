"""Private research sink: encrypt received records before writing SQLite."""
import json
import os
import sqlite3
import sys
import hashlib
from pathlib import Path
from cryptography.fernet import Fernet

os.umask(0o077)
base = Path(os.environ.get('JEGO_RESEARCH_BASE', '/opt/jego-research'))
keyfile = base / 'sms-storage.key'
try:
    with keyfile.open('xb') as handle:
        handle.write(Fernet.generate_key())
except FileExistsError:
    pass
crypto = Fernet(keyfile.read_bytes())
account = Path(os.environ.get('JEGO_CREDENTIAL_FILE', str(base / 'sdk-login-input.txt'))).read_text().splitlines()[0]
db = sqlite3.connect(base / 'research-sms.sqlite3')
db.execute('PRAGMA journal_mode=WAL')
db.execute('CREATE TABLE IF NOT EXISTS received (id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE, encrypted_record BLOB NOT NULL, ingested_at TEXT DEFAULT CURRENT_TIMESTAMP)')
db.execute('PRAGMA busy_timeout=10000')
db.execute('CREATE TABLE IF NOT EXISTS received_calls (id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE, encrypted_record BLOB NOT NULL, ingested_at TEXT DEFAULT CURRENT_TIMESTAMP)')
for line in sys.stdin:
    item = json.loads(line)
    if item.get('kind') == 'call':
        record = {'account':account, 'sender':bytes.fromhex(item['sender']).decode('utf-8','replace'), 'call_id':bytes.fromhex(item['call_id']).decode('utf-8','replace'), 'event':item['event'], 'state':item['state'], 'source_time':item['time']}
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
        fingerprint = hashlib.sha256(encoded).hexdigest()
        db.execute('INSERT OR IGNORE INTO received_calls(fingerprint,encrypted_record) VALUES (?,?)', (fingerprint, crypto.encrypt(encoded)))
        db.commit()
        continue
    record = {k: bytes.fromhex(item[k]).decode('utf-8', 'replace')
              for k in ['sender', 'time', 'gateway', 'body']}
    record.update(account=account, offline=bool(item['offline']))
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
    # Delivery flags and gateway routing may change during a replay.
    stable = {k:record[k] for k in ('account','sender','body','time')}
    fingerprint = hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO received (fingerprint,encrypted_record) VALUES (?,?)',
               (fingerprint, crypto.encrypt(encoded)))
    db.commit()
