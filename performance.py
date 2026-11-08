"""Persisted per-group API pacing. Reservations are conservative (before calling API).

This limits this application's calls, not other callers using the same RapidAPI key.
"""
import time
from core import utcnow


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS group_request_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      group_id INTEGER NOT NULL,
      job_id INTEGER NOT NULL,
      kind TEXT NOT NULL,
      at_epoch REAL NOT NULL,
      elapsed_seconds REAL,
      success INTEGER
    );
    CREATE INDEX IF NOT EXISTS group_request_log_window ON group_request_log(group_id,at_epoch);
    CREATE TABLE IF NOT EXISTS group_speed_settings (
      group_id INTEGER PRIMARY KEY,
      requests_per_minute INTEGER NOT NULL DEFAULT 24,
      fast_page_size INTEGER NOT NULL DEFAULT 100,
      about_batch INTEGER NOT NULL DEFAULT 1,
      cooldown_until REAL NOT NULL DEFAULT 0,
      last_error TEXT DEFAULT ''
    );
    ''')
    db.commit()


def configure(db,gid, rpm=24,page_size=100,about_batch=1):
    initialize(db)
    rpm=int(rpm);page_size=int(page_size);about_batch=int(about_batch)
    if not(1<=rpm<=120 and 1<=page_size<=200 and 1<=about_batch<=10):
        raise ValueError('إعدادات الأداء خارج النطاق الآمن')
    db.execute('''INSERT INTO group_speed_settings(group_id,requests_per_minute,fast_page_size,about_batch)
        VALUES(?,?,?,?) ON CONFLICT(group_id) DO UPDATE SET
        requests_per_minute=excluded.requests_per_minute,
        fast_page_size=excluded.fast_page_size,about_batch=excluded.about_batch''',
        (gid,rpm,page_size,about_batch))
    db.commit()


def settings(db,gid):
    initialize(db)
    row=db.execute('SELECT * FROM group_speed_settings WHERE group_id=?',(gid,)).fetchone()
    if row is None:
        configure(db,gid)
        row=db.execute('SELECT * FROM group_speed_settings WHERE group_id=?',(gid,)).fetchone()
    return dict(row)


def available(db,gid,now=None):
    now=time.time() if now is None else float(now)
    s=settings(db,gid)
    if now<s['cooldown_until']:
        return 0, max(0,int(s['cooldown_until']-now))
    total=db.execute('SELECT COUNT(*) FROM group_request_log WHERE group_id=? AND at_epoch>?',
                     (gid,now-60)).fetchone()[0]
    free=max(0,s['requests_per_minute']-total)
    if free:
        return free, 0
    oldest=db.execute('SELECT MIN(at_epoch) FROM group_request_log WHERE group_id=? AND at_epoch>?',
                      (gid,now-60)).fetchone()[0]
    return 0,max(1,int(oldest+60-now+0.999)) if oldest else 1


def reserve(db,gid,jid,kind,amount=1):
    """Atomic-ish single-process reservations before network I/O; all calls are counted.

    Use exactly one page per followers reservation (fetch_batch page_limit=1).
    About reserve count equals run_batch limit: unused reservations err toward safety.
    """
    free,_=available(db,gid)
    n=min(max(0,int(amount)),free)
    if n:
        now=time.time()
        db.executemany('INSERT INTO group_request_log(group_id,job_id,kind,at_epoch) VALUES(?,?,?,?)',
                       [(gid,jid,kind,now) for _ in range(n)])
        db.commit()
    return n


def observe(db,gid,jid,kind,seconds,success=True):
    """Annotate a recent reservation, recording timings without a second billable request."""
    db.execute('''UPDATE group_request_log SET elapsed_seconds=?,success=? WHERE id=(
        SELECT id FROM group_request_log WHERE group_id=? AND job_id=? AND kind=?
          AND elapsed_seconds IS NULL ORDER BY id LIMIT 1)''',
        (max(0,float(seconds)),int(bool(success)),gid,jid,kind))
    db.commit()


def cooldown(db,gid,seconds,message):
    settings(db,gid)
    db.execute('UPDATE group_speed_settings SET cooldown_until=?,last_error=? WHERE group_id=?',
               (time.time()+int(seconds),str(message)[:180],gid))
    db.commit()


def metrics(db,gid):
    s=settings(db,gid)
    free,wait=available(db,gid)
    recent=db.execute('''SELECT kind,COUNT(*) n, SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) successes,
       AVG(elapsed_seconds) avg_seconds FROM group_request_log
       WHERE group_id=? AND at_epoch>? GROUP BY kind''',(gid,time.time()-300)).fetchall()
    return {'rpm_limit':s['requests_per_minute'],'remaining_window':free,
        'wait_seconds':wait,'cooldown':max(0,round(s['cooldown_until']-time.time())),
        'last_error':s['last_error'],'recent':{r['kind']:dict(r) for r in recent}}
