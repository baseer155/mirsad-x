"""Local diagnostics. No external calls, credentials, or raw follower data."""
import sqlite3
from datetime import datetime, timezone


def db_health(db):
    try:
        check = db.execute('PRAGMA quick_check').fetchone()[0]
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        for table in ('jobs', 'followers', 'events', 'target_groups', 'about_attempts'):
            counts[table] = int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) if table in names else None
        groups = []
        if 'target_groups' in names:
            for r in db.execute('SELECT status, COUNT(*) AS n FROM target_groups GROUP BY status'):
                groups.append({'الحالة': str(r[0]), 'العدد': int(r[1])})
        return {'ok': check == 'ok', 'quick_check': check,
                'database_size_bytes': _file_bytes(db), 'counts': counts, 'groups': groups,
                'checked_utc': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    except (sqlite3.Error, OSError) as err:
        return {'ok': False, 'error_type': type(err).__name__}


def _file_bytes(db):
    # page_count/page_size reflects DB pages, not WAL size or disk free space.
    return int(db.execute('PRAGMA page_count').fetchone()[0]) * int(db.execute('PRAGMA page_size').fetchone()[0])


def security_flags(*, password_set, api_key_set, database_path, external_worker):
    from pathlib import Path
    local = database_path in ('', 'mirsad.sqlite3') or (not Path(database_path).is_absolute())
    return [
        ('دخول المسؤول', bool(password_set), 'اضبط ADMIN_PASSWORD في Secrets'),
        ('مفتاح RapidAPI', bool(api_key_set), 'احفظ المفتاح في Secrets ولا ترفعه إلى GitHub'),
        ('مسار تخزين مخصص', not local, 'مسار SQLite النسبي قد يكون مؤقتًا؛ حتى المسار المطلق لا يضمن ديمومة القرص'),
        ('عامل مستقل', bool(external_worker), 'تشغيل Streamlit وحده لا يضمن العمل بعد إغلاق الصفحة'),
    ]
