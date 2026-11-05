"""Resumable, request-budgeted About enrichment for Mirsad.
No background runner: each explicit user action processes at most 50 requests.
"""
from datetime import datetime, timezone, timedelta
from time import monotonic
from core import utcnow, add_event, safe_username
from rapid_client import lookup_about, RapidAPIError

BATCH_SIZE = 50
MAX_RETRIES = 2  # initial request + up to two transient retries
RETRY_HOURS = 1


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS about_policy (
      job_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,
      max_requests INTEGER NOT NULL DEFAULT 200,
      unit_price_usd REAL NOT NULL DEFAULT 0,
      max_estimated_usd REAL NOT NULL DEFAULT 0,
      used_requests INTEGER NOT NULL DEFAULT 0,
      FOREIGN KEY(job_id) REFERENCES jobs(id)
    );
    CREATE TABLE IF NOT EXISTS about_attempts (
      job_id INTEGER NOT NULL, uid TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      state TEXT NOT NULL DEFAULT 'pending',
      retry_after TEXT, last_error TEXT, last_attempt_at TEXT,
      PRIMARY KEY(job_id,uid), FOREIGN KEY(job_id,uid) REFERENCES followers(job_id,uid)
    );
    CREATE INDEX IF NOT EXISTS about_attempts_status_idx ON about_attempts(job_id,state,retry_after);
    ''')
    db.commit()


def policy(db, job_id):
    initialize(db)
    db.execute('INSERT OR IGNORE INTO about_policy(job_id) VALUES(?)', (job_id,))
    db.commit()
    return dict(db.execute('SELECT * FROM about_policy WHERE job_id=?',(job_id,)).fetchone())


def set_policy(db,job_id,enabled=True,max_requests=200,unit_price_usd=0.0,max_estimated_usd=0.0):
    policy(db,job_id)
    max_requests=int(max_requests); unit_price_usd=float(unit_price_usd); max_estimated_usd=float(max_estimated_usd)
    if not 1<=max_requests<=200000 or not 0<=unit_price_usd<=100 or not 0<=max_estimated_usd<=1000000:
        raise ValueError('إعدادات الميزانية غير صالحة')
    db.execute('UPDATE about_policy SET enabled=?,max_requests=?,unit_price_usd=?,max_estimated_usd=? WHERE job_id=?',
        (int(bool(enabled)),max_requests,unit_price_usd,max_estimated_usd,job_id))
    db.commit()


def _synchronize(db,job_id):
    # Old records already checked remain done. New/old unchecked rows join queue automatically.
    db.execute('''INSERT OR IGNORE INTO about_attempts(job_id,uid,state)
      SELECT job_id,uid,CASE WHEN TRIM(COALESCE(about_checked_at,''))<>'' THEN 'done'
        WHEN TRIM(COALESCE(username,''))='' THEN 'missing_username' ELSE 'pending' END
      FROM followers WHERE job_id=?''',(job_id,))
    db.execute('''UPDATE about_attempts SET state='pending',retry_after=NULL
      WHERE job_id=? AND state='missing_username' AND uid IN
      (SELECT uid FROM followers WHERE job_id=? AND TRIM(COALESCE(username,''))<>'')''',(job_id,job_id))
    db.commit()


def stats(db,job_id):
    p=policy(db,job_id);_synchronize(db,job_id)
    total=db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?',(job_id,)).fetchone()[0]
    done=db.execute("SELECT COUNT(*) FROM about_attempts WHERE job_id=? AND state='done'",(job_id,)).fetchone()[0]
    failed=db.execute("SELECT COUNT(*) FROM about_attempts WHERE job_id=? AND state='retry'",(job_id,)).fetchone()[0]
    exhausted=db.execute("SELECT COUNT(*) FROM about_attempts WHERE job_id=? AND state='exhausted'",(job_id,)).fetchone()[0]
    missing=db.execute("SELECT COUNT(*) FROM about_attempts WHERE job_id=? AND state='missing_username'",(job_id,)).fetchone()[0]
    with_country=db.execute("SELECT COUNT(*) FROM followers WHERE job_id=? AND TRIM(COALESCE(account_country,''))<>''",(job_id,)).fetchone()[0]
    left=max(0,p['max_requests']-p['used_requests'])
    if p['unit_price_usd']>0 and p['max_estimated_usd']>0:
        left=min(left,max(0,int((p['max_estimated_usd']-p['used_requests']*p['unit_price_usd']+1e-9)/p['unit_price_usd'])))
    return {**p,'total':total,'done':done,'retry':failed,'exhausted':exhausted,'missing_username':missing,
            'with_country':with_country,'remaining_requests':left,
            'estimated_usd':round(p['used_requests']*p['unit_price_usd'],4)}


def _reserve(db,job_id):
    # SQLite conditional update: never exceed the configured hard request budget.
    changed=db.execute('''UPDATE about_policy SET used_requests=used_requests+1
      WHERE job_id=? AND enabled=1 AND used_requests<max_requests
        AND (max_estimated_usd=0 OR unit_price_usd=0 OR
             (used_requests+1)*unit_price_usd<=max_estimated_usd+0.00000001)''',(job_id,)).rowcount
    db.commit()
    return bool(changed)


def _error_class(err):
    message=str(err)
    if any(code in message for code in ('401','403','404')): return 'permanent'
    if any(code in message for code in ('429','timeout','Timeout','فشل الاتصال','500','502','503','504')):return 'temporary'
    return 'permanent'


def run_batch(db,job_id,key,path='/about-account',limit=50,fetcher=None):
    """At most 50 paid requests in one synchronous click; persists all progress.

    Inject fetcher(username,key,path) in tests. For manual retries wait for retry_after.
    """
    limit=min(BATCH_SIZE,max(0,int(limit)))
    before=stats(db,job_id)
    outcome={'attempted':0,'completed':0,'with_country':0,'no_fields':0,'temporary_errors':0,
             'permanent_errors':0,'budget_exhausted':False,'throttled':False}
    if not before['enabled'] or not key or limit==0:return outcome
    fetcher=fetcher or (lambda username,key,path:lookup_about(username,key,path))
    now=utcnow()
    candidates=db.execute('''SELECT a.uid,a.attempts,a.state,f.username,f.account_country,f.connected_via,f.suspension_status
      FROM about_attempts a JOIN followers f ON f.job_id=a.job_id AND f.uid=a.uid
      WHERE a.job_id=? AND TRIM(COALESCE(f.username,''))<>''
        AND (a.state='pending' OR (a.state='retry' AND (a.retry_after IS NULL OR a.retry_after<=?)))
      ORDER BY CASE WHEN a.state='pending' THEN 0 ELSE 1 END,f.rowid LIMIT ?''',(job_id,now,limit)).fetchall()
    started=monotonic()
    for rec in candidates:
        # Streamlit synchronous run has a time limit; saved progress continues on next click.
        if monotonic()-started>75:
            outcome['time_limit_reached']=True;break
        if not _reserve(db,job_id):
            outcome['budget_exhausted']=True;break
        uid=rec['uid'];tries=rec['attempts']+1
        db.execute('''UPDATE about_attempts SET attempts=?,state='in_progress',last_attempt_at=? WHERE job_id=? AND uid=?''',(tries,utcnow(),job_id,uid))
        db.commit()
        outcome['attempted']+=1
        try:
            got=fetcher(safe_username(rec['username']),key,path)
            if not isinstance(got,dict):raise RapidAPIError('صيغة الاستجابة غير متوقعة')
            vals=[got.get(k) or rec[k] for k in ('account_country','connected_via','suspension_status')]
            db.execute('''UPDATE followers SET account_country=?,connected_via=?,suspension_status=?,about_checked_at=? WHERE job_id=? AND uid=?''',(*vals,utcnow(),job_id,uid))
            db.execute("UPDATE about_attempts SET state='done',retry_after=NULL,last_error=NULL WHERE job_id=? AND uid=?",(job_id,uid))
            db.commit()
            outcome['completed']+=1
            if vals[0]:outcome['with_country']+=1
            if not any(vals):outcome['no_fields']+=1
        except RapidAPIError as exc:
            kind=_error_class(exc)
            retry=kind=='temporary' and tries<=MAX_RETRIES
            if retry:
                retry_time=(datetime.now(timezone.utc)+timedelta(hours=RETRY_HOURS*tries)).isoformat(timespec='seconds')
                state='retry';outcome['temporary_errors']+=1
            else:
                retry_time=None;state='exhausted';outcome['permanent_errors']+=1
            db.execute('''UPDATE about_attempts SET state=?,retry_after=?,last_error=? WHERE job_id=? AND uid=?''',
                (state,retry_time,str(exc)[:250],job_id,uid))
            db.commit()
            if '429' in str(exc) or kind=='permanent':
                outcome['throttled']=True
                break  # never consume hundreds of paid requests for global auth/rate failures
    if outcome['attempted']:
        add_event(db,job_id,'إثراء About تلقائي',f"طلبات {outcome['attempted']} | مكتملة {outcome['completed']} | أخطاء مؤقتة {outcome['temporary_errors']} | أخطاء نهائية {outcome['permanent_errors']}")
    return outcome


def reset_interrupted(db,job_id):
    """Recovery from server restart mid-request; counted against budget and retried at most twice."""
    initialize(db)
    rows=db.execute("SELECT uid,attempts FROM about_attempts WHERE job_id=? AND state='in_progress'",(job_id,)).fetchall()
    for r in rows:
        state='retry' if r['attempts']<=MAX_RETRIES else 'exhausted'
        db.execute("UPDATE about_attempts SET state=?,retry_after=NULL,last_error='انقطاع أثناء الطلب' WHERE job_id=? AND uid=?",(state,job_id,r['uid']))
    db.commit()
    return len(rows)
