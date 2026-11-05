"""One safe, resumable, bounded cycle for Mirsad auto-run.

No external network access at import time. The UI invokes step() periodically with
Streamlit fragments; this is *not* a server-side job queue.
"""
from datetime import datetime, timezone
from core import add_event
from auto_enrich import policy, stats, run_batch
from rapid_client import fetch_batch, RapidAPIError


def unique_count(db, job_id):
    return db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?', (job_id,)).fetchone()[0]


def snapshot(db, job_id, goal):
    job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if job is None:
        raise ValueError('المهمة غير موجودة')
    found = unique_count(db, job_id)
    a = stats(db, job_id)
    start = datetime.fromisoformat(job['started_at'])
    elapsed = max(0., (datetime.now(timezone.utc)-start).total_seconds())
    # Rate is approximate and includes inactivity; never promise a finish time.
    rate = found/elapsed if elapsed else 0.
    eta = (goal-found)/rate if rate>0 and found<goal else None
    return {'unique':found,'goal':goal,'pages':job['pages'], 'scanned':job['scanned'],
            'duplicates':job['duplicates'],'job_status':job['status'],
            'elapsed_wall_seconds':elapsed,'rate_per_second':rate,
            'eta_seconds':eta,'about':a}


def step(db, job_id, *, rapid_key, followers_path, about_path,
         goal, page_limit=3, page_count=20, about_batch=50,
         do_fetch=True, do_about=True,
         follower_fetcher=None, about_fetcher=None):
    """Run at most one bounded fetch stage + one bounded About stage.

    Designed to be invoked once per UI timer tick, then return control to UI.
    On quota/auth/anti-loop errors returns paused/error and never retries forever.
    All attempted About calls are counted against persisted budget.
    """
    goal = int(goal)
    if goal<1:raise ValueError('الهدف يجب أن يكون موجبًا')
    snap=snapshot(db,job_id,goal)
    outcome={'fetched_pages':0,'about_attempted':0,'about_completed':0,
             'state':'running','message':'','snapshot':snap}
    at_target=snap['unique']>=goal
    no_more_pages=snap['job_status']=='مكتمل'
    if do_fetch and not at_target and not no_more_pages:
        try:
            fn=follower_fetcher or fetch_batch
            outcome['fetched_pages']=fn(db,job_id,rapid_key,followers_path,
                                       int(page_limit),goal,int(page_count))
        except RapidAPIError as exc:
            outcome.update(state='error',message=str(exc))
            add_event(db,job_id,'إيقاف تلقائي','خطأ السحب: '+str(exc)[:250])
            outcome['snapshot']=snapshot(db,job_id,goal)
            return outcome
    snap=snapshot(db,job_id,goal)
    if do_about and policy(db,job_id)['enabled'] and snap['about']['remaining_requests']>0:
        try:
            r=run_batch(db,job_id,rapid_key,about_path,int(about_batch),fetcher=about_fetcher)
            outcome['about_attempted']=r['attempted']
            outcome['about_completed']=r['completed']
            if r.get('throttled'):
                outcome.update(state='paused',message='تم إيقاف الطلبات بسبب تقييد أو خطأ في خدمة About')
        except Exception as exc:
            outcome.update(state='error',message='تعذر إثراء About: '+str(exc)[:250])
    snap=snapshot(db,job_id,goal)
    outcome['snapshot']=snap
    at_target=snap['unique']>=goal
    no_more_pages=snap['job_status']=='مكتمل'
    a=snap['about']
    # No more eligible About requests if no username, budget exhausted, or delayed retry.
    if outcome['state']=='running':
        if (at_target or no_more_pages) and (not do_about or not a['enabled'] or a['done']+a['exhausted']+a['missing_username']>=a['total']):
            outcome.update(state='complete',message='وصلت المهمة إلى الحد أو انتهت الصفحات، واكتمل الممكن من الإثراء')
        elif (at_target or no_more_pages) and a['remaining_requests']==0:
            outcome.update(state='paused',message='انتهت ميزانية طلبات About المحفوظة')
        elif (at_target or no_more_pages) and outcome['about_attempted']==0:
            outcome.update(state='paused',message='لا توجد حسابات مؤهلة للفحص الآن؛ قد تنتظر إعادة المحاولة أو بيانات يوزر')
        elif not (at_target or no_more_pages) and outcome['fetched_pages']==0:
            outcome.update(state='paused',message='لم يتم جلب صفحات جديدة؛ راجع حالة المصدر')
    return outcome
