"""Conservative, opt-in local retention for Mirsad SQLite.

A final-report user must explicitly confirm that an OFF-SERVER backup/report is saved.
Default behavior is dry-run; no follow-up jobs are silently dropped.
"""
import sqlite3
from datetime import datetime, timezone, timedelta

ISO = '%Y-%m-%dT%H:%M:%S+00:00'


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def setup(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS retention_archive (
       job_id INTEGER PRIMARY KEY, archive_confirmed_at TEXT NOT NULL,
       eligible_after TEXT NOT NULL, audit_id TEXT NOT NULL,
       target TEXT NOT NULL, updated_at_at_confirmation TEXT NOT NULL,
       follower_count INTEGER NOT NULL, backup_confirmed INTEGER NOT NULL,
       report_confirmed INTEGER NOT NULL, cleaned_at TEXT,
       FOREIGN KEY(job_id) REFERENCES jobs(id));
    CREATE TABLE IF NOT EXISTS retention_totals (
       job_id INTEGER PRIMARY KEY, target TEXT NOT NULL,
       archived_date TEXT NOT NULL, scanned INTEGER NOT NULL,
       unique_followers INTEGER NOT NULL, duplicates INTEGER NOT NULL,
       pages INTEGER NOT NULL, about_done INTEGER NOT NULL,
       with_country INTEGER NOT NULL, elapsed_seconds REAL NOT NULL,
       FOREIGN KEY(job_id) REFERENCES jobs(id));
    CREATE TABLE IF NOT EXISTS retention_runs (
       id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT NOT NULL,
       mode TEXT NOT NULL, eligible INTEGER NOT NULL, cleaned INTEGER NOT NULL,
       followers_deleted INTEGER NOT NULL, message TEXT NOT NULL);
    ''')
    db.commit()


def _group_is_running(db, job_id):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'target_groups','target_group_members'} <= tables:
        return False
    return db.execute('''SELECT 1 FROM target_group_members m JOIN target_groups g ON g.id=m.group_id
        WHERE m.job_id=? AND g.status IN ('قيد التشغيل','يعمل','running','active','قابل للاستكمال') LIMIT 1''',(job_id,)).fetchone() is not None


def confirm_archived(db, job_id, *, backup_saved=False, report_saved=False, retention_hours=24):
    """Only finalize when operator confirms external backup/report and audit is approved."""
    from report_audit import manifest_for_job
    setup(db)
    if not (backup_saved and report_saved):
        raise ValueError('يلزم تأكيد حفظ نسخة احتياطية وتقرير نهائي خارج الاستضافة أولًا')
    if retention_hours < 24:
        raise ValueError('فترة الاحتفاظ لا تقل عن 24 ساعة')
    if _group_is_running(db,job_id):
        raise ValueError('المهمة مرتبطة بمجموعة لا تزال تعمل')
    report=manifest_for_job(db,job_id)
    if not report['approved']:
        raise ValueError('التقرير غير معتمد: '+str(report['reason']))
    job=db.execute('SELECT id,target,updated_at FROM jobs WHERE id=?',(job_id,)).fetchone()
    n=int(db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?',(job_id,)).fetchone()[0])
    if db.execute('SELECT 1 FROM retention_archive WHERE job_id=? AND cleaned_at IS NOT NULL',(job_id,)).fetchone():
        raise ValueError('تم تنظيف المهمة مسبقًا')
    t=datetime.now(timezone.utc)
    db.execute('''INSERT INTO retention_archive
      (job_id,archive_confirmed_at,eligible_after,audit_id,target,updated_at_at_confirmation,follower_count,backup_confirmed,report_confirmed)
      VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
      archive_confirmed_at=excluded.archive_confirmed_at, eligible_after=excluded.eligible_after,
      audit_id=excluded.audit_id,target=excluded.target,updated_at_at_confirmation=excluded.updated_at_at_confirmation,
      follower_count=excluded.follower_count,backup_confirmed=1,report_confirmed=1''',
      (job_id,t.isoformat(timespec='seconds'),(t+timedelta(hours=retention_hours)).isoformat(timespec='seconds'),
       report['audit_id'],job['target'],job['updated_at'],n,1,1))
    db.commit()
    return report


def candidates(db, *, at=None):
    setup(db)
    at=at or now_utc()
    rows=db.execute('''SELECT a.*,j.status,j.updated_at FROM retention_archive a
       JOIN jobs j ON j.id=a.job_id
       WHERE a.cleaned_at IS NULL AND a.eligible_after<=? AND a.backup_confirmed=1
       AND a.report_confirmed=1 ORDER BY a.eligible_after''',(at,)).fetchall()
    results=[]
    for r in rows:
        jid=r['job_id']
        if r['status']!='مكتمل' or r['updated_at']!=r['updated_at_at_confirmation'] or _group_is_running(db,jid):
            continue
        count=db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?',(jid,)).fetchone()[0]
        if count!=r['follower_count']:
            continue
        pending=db.execute('''SELECT COUNT(*) FROM about_attempts
                   WHERE job_id=? AND state!='done' ''',(jid,)).fetchone()[0] if _exists(db,'about_attempts') else 0
        if pending:
            continue
        results.append(dict(r))
    return results


def _exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None


def clean_due(db, *, dry_run=True, at=None):
    """Transactional cleanup; processed only on explicit run or an external scheduler."""
    eligible=candidates(db,at=at)
    if dry_run:
        return {'eligible':len(eligible),'cleaned':0,'followers_deleted':0,'ids':[r['job_id'] for r in eligible]}
    cleaned=0; deleted=0
    for r in eligible:
        jid=r['job_id']
        try:
            db.execute('BEGIN IMMEDIATE')
            # Check simple state again under lock; archive only after approval/backup.
            row=db.execute('SELECT status,updated_at,scanned,duplicates,pages,elapsed_seconds,target FROM jobs WHERE id=?',(jid,)).fetchone()
            n=db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?',(jid,)).fetchone()[0]
            if not row or row['status']!='مكتمل' or row['updated_at']!=r['updated_at_at_confirmation'] or n!=r['follower_count'] or _group_is_running(db,jid):
                db.rollback();continue
            pending=db.execute("SELECT COUNT(*) FROM about_attempts WHERE job_id=? AND state!='done'",(jid,)).fetchone()[0] if _exists(db,'about_attempts') else 0
            if pending:
                db.rollback();continue
            about_done=db.execute("SELECT COUNT(*) FROM about_attempts WHERE job_id=? AND state='done'",(jid,)).fetchone()[0] if _exists(db,'about_attempts') else 0
            with_country=db.execute("SELECT COUNT(*) FROM followers WHERE job_id=? AND TRIM(COALESCE(account_country,''))!=''",(jid,)).fetchone()[0]
            db.execute('''INSERT OR IGNORE INTO retention_totals
              (job_id,target,archived_date,scanned,unique_followers,duplicates,pages,about_done,with_country,elapsed_seconds)
              VALUES(?,?,?,?,?,?,?,?,?,?)''',(jid,row['target'],now_utc()[:10],int(row['scanned']),n,int(row['duplicates']),int(row['pages']),about_done,with_country,float(row['elapsed_seconds'])))
            for table in ('about_attempts','followers'):
                if _exists(db,table): db.execute(f'DELETE FROM "{table}" WHERE job_id=?',(jid,))
            # keep jobs, events, about_policy, group membership, and cumulative totals.
            db.execute('UPDATE retention_archive SET cleaned_at=? WHERE job_id=? AND cleaned_at IS NULL',(now_utc(),jid))
            db.commit();cleaned+=1;deleted+=n
        except Exception:
            db.rollback()
            raise
    db.execute('INSERT INTO retention_runs(checked_at,mode,eligible,cleaned,followers_deleted,message) VALUES(?,?,?,?,?,?)',
               (now_utc(),'manual-or-scheduled',len(eligible),cleaned,deleted,'احتفاظ بالإحصاءات التاريخية'))
    db.commit()
    return {'eligible':len(eligible),'cleaned':cleaned,'followers_deleted':deleted}


def dashboard(db):
    setup(db)
    totals=db.execute('''SELECT COUNT(*) AS jobs, COALESCE(SUM(unique_followers),0) AS followers,
         COALESCE(SUM(scanned),0) AS scanned,COALESCE(SUM(about_done),0) AS about_done
         FROM retention_totals''').fetchone()
    active=db.execute('SELECT COUNT(*) FROM followers').fetchone()[0]
    last=db.execute('SELECT * FROM retention_runs ORDER BY id DESC LIMIT 1').fetchone()
    return {'current_followers':int(active),'all_time_follower_records':int(totals['followers'])+int(active), 'archived_jobs':int(totals['jobs']),'archived_followers':int(totals['followers']),
            'archived_scanned':int(totals['scanned']),'archived_about':int(totals['about_done']),
            'pending_archive':len(candidates(db)),'last_run':dict(last) if last else None}
