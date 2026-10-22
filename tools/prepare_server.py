"""Create a dedicated SMS-only source directory and bootstrap console secrets.

Run on the authorized Ubuntu server. Never prints SMS bodies or credentials.
Existing collector files are retained. SQLite backup includes committed WAL data.
"""
import json
import os
import secrets
import shutil
import sqlite3
from pathlib import Path

os.umask(0o077)
collector = Path('/opt/jego-research')
source = Path('/opt/jego-sms-source')
web = Path('/opt/jego-sms-web')
source.mkdir(mode=0o700, exist_ok=True)
web.mkdir(mode=0o700, exist_ok=True)
(web / 'data').mkdir(mode=0o700, exist_ok=True)
source_db = collector / 'research-sms.sqlite3'
if not source_db.is_file() or not (collector / 'sms-storage.key').is_file():
    raise RuntimeError('Collector SMS database/key not found')
target = source / 'research-sms.sqlite3'
if not target.exists():
    incoming = sqlite3.connect(source_db.resolve().as_uri() + '?mode=ro', uri=True)
    outgoing = sqlite3.connect(target)
    try:
        incoming.backup(outgoing)
    finally:
        outgoing.close()
        incoming.close()
    shutil.copy2(collector / 'sms-storage.key', source / 'sms-storage.key')
else:
    # Never overwrite a source that a future collector may already be writing.
    if not (source / 'sms-storage.key').is_file():
        raise RuntimeError('Existing dedicated source is missing its encryption key')
password_file = web / 'admin-initial-password.txt'
if not password_file.exists():
    password_file.write_text(secrets.token_urlsafe(24) + '\n')
    password_file.chmod(0o600)
connection = sqlite3.connect(target)
# The web container mounts this directory read-only. A rollback-journal source
# avoids requiring a writable WAL shared-memory file in that container.
connection.execute('PRAGMA journal_mode=DELETE')
count = connection.execute('SELECT COUNT(*) FROM received').fetchone()[0]
connection.close()
print(json.dumps({'dedicated_source_records': count, 'admin_credentials_created': True,
                  'collector_originals_retained': True, 'contains_app_login_tokens': False}))
