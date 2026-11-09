"""Multi-target manager for Mirsad. Independent jobs, quotas, cursors and dedup.

Only one bounded operation runs per Streamlit timer tick. No background worker.
"""
import re
from datetime import datetime, timezone
from core import create_job, add_event, username_from_input, utcnow
from auto_enrich import set_policy, stats as about_stats, reset_interrupted
from auto_runner import step
from performance import reserve, settings as speed_settings, cooldown as speed_cooldown, metrics as speed_metrics

MAX_TARGETS = 100


def parse_targets(value):
    """Accept one @handle (or x.com/handle) per line; optional legacy numeric ID.

    Returns pairs (username, uid-or-None); unresolved entries must be verified
    against the provider before a group can be created.
    """
    items, seen, seen_uid, errors = [], set(), set(), []
    for n, raw in enumerate(str(value or '').splitlines(), 1):
        raw = raw.strip()
        if not raw or raw.startswith('#'):
            continue
        parts = [x.strip() for x in re.split(r'[,;|\t]', raw)]
        if len(parts) not in (1, 2):
            errors.append(f'السطر {n}: أدخل اليوزر فقط، أو اليوزر والمعرف الرقمي')
            continue
        try:
            username=username_from_input(parts[0])
            uid=parts[1] if len(parts)==2 else None
            if uid is not None and not re.fullmatch(r'\d{1,25}', uid):
                raise ValueError('المعرف الرقمي غير صالح')
            if username.lower() in seen or (uid is not None and uid in seen_uid):
                errors.append(f'السطر {n}: الحساب @{username} مكرر')
                continue
            seen.add(username.lower())
            if uid is not None: seen_uid.add(uid)
            items.append((username,uid))
        except ValueError as exc:
            errors.append(f'السطر {n}: {exc}')
    if len(items)>MAX_TARGETS:
        errors.append(f'الحد الأقصى {MAX_TARGETS} حساب في المجموعة الواحدة')
    if not items:
        errors.append('لم تُدخل حسابات صالحة')
    return items,errors


def resolve_targets(targets, key, path='/user', fetcher=None):
    """Verify handles and IDs, returning profiles to cache with the group.

    No jobs are created until *all* accounts resolve; no silent partial imports.
    """
    from rapid_client import lookup_profile_by_username
    resolver=fetcher or lookup_profile_by_username
    confirmed=[];errors=[];uid_seen=set()
    for username, supplied_id in targets:
        try:
            profile=resolver(username,key,path)
            if not isinstance(profile,dict):raise ValueError('استجابة ملف الحساب غير صالحة')
            uid=str(profile.get('id',''))
            handle=str(profile.get('username','')).lstrip('@')
            if not uid.isdigit() or handle.casefold()!=username.casefold():
                raise ValueError('عدم تطابق اليوزر أو المعرّف الرقمي من المصدر')
            if supplied_id is not None and supplied_id!=uid:
                raise ValueError('المعرف الدائم المُدخل لا يطابق حساب X')
            if uid in uid_seen:raise ValueError('المعرف الدائم مكرر في المجموعة')
            uid_seen.add(uid)
            count=profile.get('followers_count')
            if count is not None:
                count=int(count)
                if count<0: count=None
            confirmed.append((username,uid,count))
        except Exception as exc:
            # No raw provider response or secret token is ever displayed.
            errors.append(f'@{username}: {str(exc)[:160] if isinstance(exc, ValueError) else type(exc).__name__}')
    return confirmed, errors


def initialize(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS target_groups (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'جاهز',
       goal_per_target INTEGER NOT NULL, page_cap INTEGER NOT NULL,
       page_size INTEGER NOT NULL, pages_per_tick INTEGER NOT NULL DEFAULT 1,
       about_per_tick INTEGER NOT NULL DEFAULT 10,
       created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
       last_error TEXT
    );
    CREATE TABLE IF NOT EXISTS target_group_members (
       group_id INTEGER NOT NULL, job_id INTEGER NOT NULL UNIQUE,
       ordinal INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'جاهز',
       stall_count INTEGER NOT NULL DEFAULT 0,
       message TEXT,
       PRIMARY KEY(group_id,job_id),
       FOREIGN KEY(group_id) REFERENCES target_groups(id),
       FOREIGN KEY(job_id) REFERENCES jobs(id)
    );
    CREATE INDEX IF NOT EXISTS group_member_idx ON target_group_members(group_id,state,ordinal);
    ''')
    cols={r[1] for r in db.execute('PRAGMA table_info(target_group_members)').fetchall()}
    for name,typename in [('follower_total','INTEGER'),('follower_total_checked_at','TEXT'),('follower_total_error','TEXT')]:
        if name not in cols: db.execute(f'ALTER TABLE target_group_members ADD COLUMN {name} {typename}')
    db.commit()


def create_group(db, name, targets, *, goal, page_cap, page_size, pages_per_tick=1,
                 about_per_tick=10, about_enabled=True, about_budget=None):
    initialize(db)
    goal, page_cap, page_size = int(goal), int(page_cap), int(page_size)
    pages_per_tick, about_per_tick = int(pages_per_tick), int(about_per_tick)
    # About expands with detected follower list; no manually typed enrichment goal.
    about_budget = min(200000,max(1,int(about_budget if about_budget is not None else goal)))
    if not (1 <= len(targets) <= MAX_TARGETS and 1 <= goal <= 1000000
            and 1 <= page_cap <= 100000 and 1 <= page_size <= 200
            and 1 <= pages_per_tick <= 5 and 1 <= about_per_tick <= 50
            and 1 <= about_budget <= 200000):
        raise ValueError('إعدادات المجموعة غير صالحة')
    if any(not t[1] or not str(t[1]).isdigit() for t in targets):
        raise ValueError('لم يُتحقق من معرفات جميع الحسابات بعد')
    if len({t[0].lower() for t in targets}) != len(targets) or len({t[1] for t in targets}) != len(targets):
        raise ValueError('هناك أسماء حسابات أو معرفات دائمة مكررة')
    now = utcnow()
    # Transaction means a failed creation cannot leave half a group. create_job()
    # commits internally, so validate everything before creating records.
    cur = db.execute('''INSERT INTO target_groups(name,status,goal_per_target,page_cap,page_size,
        pages_per_tick,about_per_tick,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)''',
        (str(name or 'مجموعة حسابات').strip()[:120], 'جاهز', goal, page_cap,
         page_size, pages_per_tick, about_per_tick, now, now))
    gid = cur.lastrowid
    db.commit()
    for i, target_info in enumerate(targets):
        username,uid=target_info[:2]
        follower_total=target_info[2] if len(target_info)>2 else None
        jid = create_job(db, 'X', username, uid)
        db.execute('''INSERT INTO target_group_members(group_id,job_id,ordinal,follower_total,follower_total_checked_at)
          VALUES(?,?,?,?,?)''', (gid,jid,i,follower_total,now if len(target_info)>2 else None))
        db.commit()
        if about_enabled:
            set_policy(db,jid,True,about_budget,0,0)
        else:
            set_policy(db,jid,False,about_budget,0,0)
        add_event(db,jid,'إنشاء مجموعة',f'المجموعة #{gid} | الحساب @{username}')
    return gid


def get_groups(db):
    initialize(db)
    return db.execute('SELECT * FROM target_groups ORDER BY id DESC').fetchall()


def get_group(db, group_id):
    initialize(db)
    return db.execute('SELECT * FROM target_groups WHERE id=?',(group_id,)).fetchone()


def members(db, group_id):
    initialize(db)
    return db.execute('''SELECT m.*, j.target,j.target_uid,j.pages,j.status AS job_status,
       j.elapsed_seconds,j.error, m.follower_total,m.follower_total_checked_at,m.follower_total_error,
       (SELECT COUNT(*) FROM followers f WHERE f.job_id=j.id) AS unique_count,
       (SELECT COUNT(*) FROM followers f WHERE f.job_id=j.id
          AND TRIM(COALESCE(f.account_country,''))<>'') AS with_country
      FROM target_group_members m JOIN jobs j ON j.id=m.job_id
      WHERE m.group_id=? ORDER BY m.ordinal''',(group_id,)).fetchall()


def set_status(db, group_id, status):
    initialize(db)
    if status not in ('جاهز','قيد التشغيل','متوقف','مكتمل'):
        raise ValueError('حالة المجموعة غير صالحة')
    db.execute('UPDATE target_groups SET status=?,updated_at=? WHERE id=?',(status,utcnow(),group_id))
    db.commit()


def resume(db, group_id):
    initialize(db)
    db.execute("UPDATE target_group_members SET state='جاهز',stall_count=0 WHERE group_id=? AND state IN ('متوقف','خطأ')",(group_id,))
    set_status(db,group_id,'قيد التشغيل')


def tick(db,group_id,*,rapid_key,followers_path,about_path,profiles_path='/get-users',fetcher=None,about_fetcher=None,profile_fetcher=None):
    """Process ONE target in round-robin, with hard per-target page/About budgets."""
    group=get_group(db,group_id)
    if group is None:
        raise ValueError('المجموعة غير موجودة')
    if group['status']!='قيد التشغيل':
        return {'state':'idle','message':'المجموعة غير نشطة'}
    rows=members(db,group_id)
    candidates=[r for r in rows if r['state']=='جاهز']
    if not candidates:
        terminal='مكتمل' if all(r['state']=='مكتمل' for r in rows) else 'متوقف'
        set_status(db,group_id,terminal)
        return {'state':'finished','message':f'انتهت المهام النشطة؛ حالة المجموعة: {terminal}'}
    # Rotate the queue by moving the processed member's ordinal to the end.
    row=candidates[0]
    jid=row['job_id']
    # Resolve follower total from the provider once per target (if available).
    if row['follower_total_checked_at'] is None and rapid_key:
        if reserve(db,group_id,jid,'target_profile',1)==0:
            return {'state':'rate_limited','message':'انتظار حصة استعلام الحساب'}
        started=__import__('time').monotonic()
        try:
            from rapid_client import lookup_profiles_by_ids
            provider=profile_fetcher or lookup_profiles_by_ids
            found=provider([str(row['target_uid'])],rapid_key,profiles_path)
            count=next((int(u['followers_count']) for u in found if str(u.get('id'))==str(row['target_uid'])
                        and u.get('followers_count') is not None),None)
            db.execute("""UPDATE target_group_members SET follower_total=?,follower_total_checked_at=?,follower_total_error=?
                          WHERE group_id=? AND job_id=?""",(count,utcnow(),None if count is not None else 'غير متاح من المصدر',group_id,jid))
            from performance import observe
            observe(db,group_id,jid,'target_profile',__import__('time').monotonic()-started,True)
        except Exception as exc:
            db.execute("""UPDATE target_group_members SET follower_total_checked_at=?,follower_total_error=?
                          WHERE group_id=? AND job_id=?""",(utcnow(),type(exc).__name__,group_id,jid))
            from performance import observe
            observe(db,group_id,jid,'target_profile',__import__('time').monotonic()-started,False)
        db.execute('UPDATE target_group_members SET ordinal=(SELECT COALESCE(MAX(ordinal),0)+1 FROM target_group_members WHERE group_id=?) WHERE group_id=? AND job_id=?',(group_id,group_id,jid))
        db.commit()
        return {'state':'profile_checked','job_id':jid,'target':row['target'],'message':'تم فحص عدد متابعي الحساب المستهدف'}
    if row['pages']>=group['page_cap'] and row['job_status']!='مكتمل':
        _mark(db,group_id,jid,'متوقف','بلغ سقف الصفحات؛ يمكن مراجعة السقف والاستكمال')
        return {'state':'paused','job_id':jid,'target':row['target'],'message':'بلغ الحساب سقف الصفحات'}
    # One API request per turn, paced by group-wide RPM across follower and About stages.
    # About is served periodically; a used-up About budget never blocks follower fetch.
    from auto_enrich import policy as about_policy, stats as about_statistics
    # Do not stop at the advertised follower count; only the last page proves completion.
    follower_pending = row['unique_count'] < group['goal_per_target'] and row['job_status'] != 'مكتمل'
    about = about_statistics(db,jid)
    about_pending = bool(about['enabled'] and about['remaining_requests'] > 0 and
                         about['done'] + about['exhausted'] + about['missing_username'] < about['total'])
    recent=speed_metrics(db,group_id)
    # Separate queue scheduling: one About call after every four follower pages.
    # Each request has its own reservation; About quota cannot block followers.
    prior=db.execute('SELECT COUNT(*) FROM group_request_log WHERE group_id=?',(group_id,)).fetchone()[0]
    use_about = about_pending and (not follower_pending or prior % 5 == 4)
    kind='about' if use_about else 'followers'
    if not follower_pending and not about_pending:
        from readiness import inspect_job
        quality=inspect_job(db,jid)
        if quality['approved']:
            _mark(db,group_id,jid,'مكتمل','تم الانتهاء من المهمة')
            return {'state':'complete','job_id':jid,'target':row['target'],'message':'تم الانتهاء من المهمة'}
        _mark(db,group_id,jid,'متوقف','بانتظار اكتمال السحب والإثراء: '+quality['reason'][:160])
        return {'state':'paused','job_id':jid,'target':row['target'],'message':quality['reason']}
    if not follower_pending and not use_about:
        use_about=True;kind='about'
    if reserve(db,group_id,jid,kind,1) == 0:
        return {'state':'rate_limited','message':'حد الطلبات المؤقت؛ سيتم الاستئناف تلقائيًا'}
    started=__import__('time').monotonic()
    try:
        reset_interrupted(db,jid)
        result=step(db,jid,rapid_key=rapid_key,followers_path=followers_path,about_path=about_path,
            goal=group['goal_per_target'],
            page_limit=1,
            page_count=speed_settings(db,group_id)['fast_page_size'],about_batch=1,
            do_fetch=not use_about,do_about=use_about,follower_fetcher=fetcher,about_fetcher=about_fetcher)
        from performance import observe
        observe(db,group_id,jid,kind,__import__('time').monotonic()-started,success=result['state']!='error')
        state=result['state']
        if state in ('error','paused') and ('429' in str(result.get('message','')) or 'rate limit' in str(result.get('message','')).lower()):
            speed_cooldown(db,group_id,60,'تقييد من مزود API؛ إعادة المحاولة بعد فترة تهدئة')
            state='running'
            result['message']='تم تفعيل التهدئة المؤقتة للمجموعة'
        stall=row['stall_count']+1 if result.get('fetched_pages',0)>0 and result.get('new_unique',0)==0 else 0
        if state=='running' and stall>=2:
            state='paused'
            result['message']='صفحتان دون متابعين جدد؛ توقف احترازي لمنع الدوران'
        if state=='complete':
            from readiness import inspect_job
            quality=inspect_job(db,jid)
            if quality['approved']:
                _mark(db,group_id,jid,'مكتمل','تم الانتهاء من المهمة')
            else:
                state='paused'
                _mark(db,group_id,jid,'متوقف',quality['reason'])
        elif state=='error':
            _mark(db,group_id,jid,'خطأ',result.get('message',''))
        elif state=='paused':
            _mark(db,group_id,jid,'متوقف',result.get('message',''))
        else:
            mx=db.execute('SELECT COALESCE(MAX(ordinal),0) FROM target_group_members WHERE group_id=?',(group_id,)).fetchone()[0]
            db.execute('UPDATE target_group_members SET ordinal=?,stall_count=? WHERE group_id=? AND job_id=?',
                (mx+1,stall,group_id,jid))
            db.commit()
        return {'state':state,'job_id':jid,'target':row['target'], 'result':result,
                'message':result.get('message',''), 'new_unique':result.get('new_unique',0),
                'about_attempted':result.get('about_attempted',0)}
    except Exception as exc:
        from performance import observe
        observe(db,group_id,jid,kind,__import__('time').monotonic()-started,success=False)
        # Do not expose secrets in exception details; still retain a minimal reason.
        message=f'فشل معالجة المهمة: {type(exc).__name__}'
        _mark(db,group_id,jid,'خطأ',message)
        return {'state':'error','job_id':jid,'target':row['target'],'message':message}


def _mark(db,gid,jid,state,message):
    db.execute('UPDATE target_group_members SET state=?,message=? WHERE group_id=? AND job_id=?',
               (state,str(message or '')[:300],gid,jid))
    db.commit()
