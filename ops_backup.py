"""SQLite-consistent snapshot and offline restoration helpers.

The backup includes account information; handle the bytes as sensitive.
"""
import io
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def backup_bytes(connection):
    # SQLite's online backup API preserves a consistent snapshot even in WAL mode.
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / 'mirsad-backup.sqlite3'
        with sqlite3.connect(str(target), timeout=30) as destination:
            connection.backup(destination, pages=128, sleep=0.05)
            assert_integrity(destination)
        return target.read_bytes()


def assert_integrity(connection):
    result = connection.execute('PRAGMA integrity_check').fetchone()[0]
    if result != 'ok':
        raise ValueError('SQLite integrity check failed')


def backup_filename():
    return 'mirsad-backup-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '.sqlite3'


def restore_offline(backup_path, destination_path, *, confirmed=False):
    """Explicit offline restore to a NEW path. Never overwrite an active database."""
    if not confirmed:
        raise PermissionError('Explicit --confirm required')
    source = Path(backup_path).resolve(strict=True)
    target = Path(destination_path).resolve()
    if source == target or target.exists():
        raise FileExistsError('Destination must be a new, non-existing file')
    if not source.is_file():
        raise ValueError('Backup is not a file')
    with sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True) as source_db:
        assert_integrity(source_db)
        with sqlite3.connect(str(target)) as new_db:
            source_db.backup(new_db)
            assert_integrity(new_db)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target
