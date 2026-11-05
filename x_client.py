"""Official X API client; uses only documented follower endpoint and cursor pagination."""
import time
import requests

BASE='https://api.x.com/2'
class XAPIError(RuntimeError):
    pass

def get(path, bearer, params=None):
    if not bearer:
        raise XAPIError('رمز X_BEARER_TOKEN غير مضبوط في أسرار الخادم.')
    try:
        res=requests.get(f'{BASE}/{path}',params=params,
            headers={'Authorization':f'Bearer {bearer}','Accept':'application/json'},timeout=(10,45))
    except requests.RequestException as exc:
        raise XAPIError(f'تعذّر الاتصال بـ X: {type(exc).__name__}') from exc
    if res.status_code==429:
        raise XAPIError('تم بلوغ حد طلبات X (429). أعد التشغيل لاحقًا؛ تم الاحتفاظ بآخر صفحة مكتملة.')
    if res.status_code in (401,403):
        raise XAPIError(f'صلاحيات X API غير كافية أو المفتاح غير صالح (HTTP {res.status_code}).')
    if not res.ok:
        raise XAPIError(f'استجابة X HTTP {res.status_code}: {res.text[:180]}')
    try:return res.json()
    except ValueError as exc:raise XAPIError('استجابة X ليست JSON صالحًا') from exc

def lookup_user(username,bearer):
    payload=get(f'users/by/username/{username}',bearer)
    if not payload.get('data',{}).get('id'):
        raise XAPIError('لم تعُد بيانات الحساب المستهدف من X.')
    return payload['data']

def followers_page(uid,bearer,cursor=None):
    params={'max_results':1000,'user.fields':'name,username,location,verified,protected,public_metrics'}
    if cursor:params['pagination_token']=cursor
    payload=get(f'users/{uid}/followers',bearer,params)
    return payload.get('data') or [], (payload.get('meta') or {}).get('next_token')

def fetch_batch(db,job_id,bearer,page_limit=5,unique_limit=50000,callback=None):
    from core import save_page,add_event,utcnow
    row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row or row['platform'] != 'X' or not row['target_uid']:
        raise XAPIError('المهمة غير صالحة للاتصال بـ X.')
    if row['status']=='مكتمل':return 0
    completed=0
    for _ in range(int(page_limit)):
        if db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?',(job_id,)).fetchone()[0]>=unique_limit:
            break
        current=db.execute('SELECT cursor FROM jobs WHERE id=?',(job_id,)).fetchone()['cursor']
        t=time.monotonic()
        try:
            users,next_cursor=followers_page(row['target_uid'],bearer,current)
        except XAPIError as exc:
            db.execute('UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?',('متوقف',str(exc),utcnow(),job_id));db.commit()
            add_event(db,job_id,'خطأ',str(exc))
            raise
        added,dups=save_page(db,job_id,users,next_cursor,time.monotonic()-t)
        completed+=1
        if callback:callback(completed,added,dups)
        if not next_cursor or not users:break
    return completed
