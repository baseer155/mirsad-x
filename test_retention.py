import os, sqlite3, sys, tempfile
from datetime import datetime, timezone, timedelta
sys.path.insert(0, '/mnt/data/mirsad_v17_release')
sys.path.insert(0, '/mnt/data/mirsad_v17_phase4_reports')
sys.path.insert(0, os.path.dirname(__file__))
from core import connect, create_job
from auto_enrich import initialize, set_policy
from ops_retention import (setup,confirm_archived,candidates,clean_due,dashboard)

with tempfile.TemporaryDirectory() as d:
    db=connect(os.path.join(d,'test.sqlite3'))
    job=create_job(db,'X','demo','111')
    initialize(db)
    set_policy(db,job,enabled=True,max_requests=200)
    db.execute("INSERT INTO followers(job_id,uid,username,name,account_country) VALUES(?,?,?,?,?)",(job,'123','person','شخص','Saudi Arabia'))
    db.execute("INSERT INTO about_attempts(job_id,uid,state) VALUES(?,?,?)",(job,'123','pending'))
    db.execute("UPDATE jobs SET status='مكتمل',scanned=1,pages=1 WHERE id=?",(job,));db.commit()
    try: confirm_archived(db,job,backup_saved=True,report_saved=True)
    except ValueError: pass
    else: raise AssertionError('pending About was approved')
    db.execute("UPDATE about_attempts SET state='done' WHERE job_id=?",(job,)); db.commit()
    try: confirm_archived(db,job,backup_saved=False,report_saved=True)
    except ValueError: pass
    else: raise AssertionError('unconfirmed external backup was allowed')
    confirm_archived(db,job,backup_saved=True,report_saved=True)
    assert candidates(db)==[]
    assert clean_due(db,dry_run=False)['cleaned']==0
    past=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat(timespec='seconds')
    db.execute('UPDATE retention_archive SET eligible_after=? WHERE job_id=?',(past,job));db.commit()
    assert clean_due(db,dry_run=True)['eligible']==1
    assert db.execute('SELECT COUNT(*) FROM followers').fetchone()[0]==1
    result=clean_due(db,dry_run=False)
    assert result['cleaned']==1 and result['followers_deleted']==1
    assert db.execute('SELECT COUNT(*) FROM followers').fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
    assert dashboard(db)['archived_followers']==1
    assert clean_due(db,dry_run=False)['cleaned']==0
    assert dashboard(db)['archived_followers']==1
    job2=create_job(db,'X','unfinished','222')
    db.execute("INSERT INTO followers(job_id,uid,username,name) VALUES(?,?,?,?)",(job2,'456','test','Test'));db.commit()
    assert db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?',(job2,)).fetchone()[0]==1
    assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    db.close()
print('PASS: 10 retention checks; SQLite integrity ok')
