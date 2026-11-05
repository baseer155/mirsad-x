"""RapidAPI twitter241 adapter. No network requests at import time.

Paths are configurable because RapidAPI providers can change routes.
Never embed API keys in source code.
"""
import time
from urllib.parse import urlsplit
import requests
from core import save_page, add_event, utcnow

HOST = 'twitter241.p.rapidapi.com'

class RapidAPIError(RuntimeError):
    pass

def validate_path(path):
    path = str(path or '').strip()
    if not path or not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:
        raise RapidAPIError('مسار RapidAPI غير مضبوط. أدخل المسار الذي يظهر في توثيق خدمة Get User Followers ضمن RAPIDAPI_FOLLOWERS_PATH.')
    if urlsplit(path).scheme or '\\' in path:
        raise RapidAPIError('مسار الاتصال غير آمن.')
    return path

def call(path, key, params):
    if not key:
        raise RapidAPIError('لم يُضبط RAPIDAPI_KEY في Secrets.')
    path = validate_path(path)
    url = 'https://' + HOST + path
    try:
        response = requests.get(url,
            headers={'X-RapidAPI-Key': key, 'X-RapidAPI-Host': HOST,
                     'Accept':'application/json'},
            params=params, timeout=(10, 45))
    except requests.RequestException as exc:
        raise RapidAPIError('فشل الاتصال بـRapidAPI: ' + type(exc).__name__) from exc
    if response.status_code in (401, 403):
        raise RapidAPIError(f'رفضت RapidAPI الوصول (HTTP {response.status_code}). تأكد من المفتاح والاشتراك والصلاحيات.')
    if response.status_code == 404:
        raise RapidAPIError('المسار غير موجود (404). راجع RAPIDAPI_FOLLOWERS_PATH.')
    if response.status_code == 429:
        raise RapidAPIError('تم بلوغ حد الطلبات (429). أوقف السحب واستكمل لاحقًا بعد تجدد الحصة.')
    if not response.ok:
        raise RapidAPIError('تعذر جلب البيانات (HTTP %s).' % response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RapidAPIError('الاستجابة ليست JSON صالحًا.') from exc
    if not isinstance(payload, dict):
        raise RapidAPIError('صيغة استجابة غير متوقعة.')
    return payload

def parse_user(raw):
    """Map multiple observed provider envelopes without inventing missing profile fields."""
    if not isinstance(raw,dict):return None
    if isinstance(raw.get('user_results'),dict):raw=raw['user_results'].get('result') or raw
    if isinstance(raw.get('result'),dict) and not raw.get('rest_id'):
        raw=raw['result']
    legacy=raw.get('legacy') or {}
    if not isinstance(legacy,dict):legacy={}
    uid=raw.get('rest_id') or raw.get('id_str') or raw.get('user_id') or raw.get('uid')
    # GraphQL id is base64 opaque; never use it as a numeric user ID.
    if not uid and str(raw.get('id','')).isdigit():uid=raw['id']
    if not uid or not str(uid).isdigit():return None
    metrics=raw.get('public_metrics') or {}
    return {'id':str(uid),
            'username':legacy.get('screen_name') or legacy.get('username') or raw.get('screen_name') or raw.get('username') or '',
            'name':legacy.get('name') or raw.get('name') or '',
            'location':legacy.get('location') or raw.get('location') or '',
            'protected':legacy.get('protected') if legacy.get('protected') is not None else raw.get('protected'),
            'verified':raw.get('is_blue_verified') if raw.get('is_blue_verified') is not None else (legacy.get('verified') if legacy.get('verified') is not None else raw.get('verified')),
            'followers_count':legacy.get('followers_count') if legacy.get('followers_count') is not None else (raw.get('followers_count') if raw.get('followers_count') is not None else metrics.get('followers_count')),
            'following_count':legacy.get('friends_count') if legacy.get('friends_count') is not None else (raw.get('friends_count') if raw.get('friends_count') is not None else metrics.get('following_count'))}

def extract_users(payload):
    """Extract profile objects from followers responses and saved JSON test files."""
    if not isinstance(payload,dict):raise RapidAPIError('الملف ليس JSON object.')
    result=payload.get('result') or {}
    timeline=result.get('timeline') or {} if isinstance(result,dict) else {}
    instructions=timeline.get('instructions') or []
    bottom=(payload.get('cursor') or {}).get('bottom') if isinstance(payload.get('cursor'),dict) else None
    users=[]
    for inst in instructions:
        for entry in inst.get('entries') or []:
            content=entry.get('content') or {}
            if content.get('cursorType')=='Bottom':bottom=bottom or content.get('value')
            raw=((content.get('itemContent') or {}).get('user_results') or {}).get('result') or {}
            user=parse_user(raw)
            if user:users.append(user)
    if users:return users,(str(bottom) if bottom else None)
    # Provider alternatives: flat data arrays, user lists, and individual results.
    for source in (payload, result if isinstance(result,dict) else {},payload.get('data') if isinstance(payload.get('data'),dict) else {}):
        if not isinstance(source,dict):continue
        for field in ('users','followers','entries','data'):
            values=source.get(field)
            if not isinstance(values,list):continue
            for raw in values:
                user=parse_user(raw)
                if user:users.append(user)
    if not users and not instructions and 'result' not in payload and not isinstance(payload.get('users'),list):
        raise RapidAPIError('لا تتطابق استجابة المزود مع الشكل المعتمد.')
    return users,(str(bottom) if bottom else None)

def parse_page(payload):
    return extract_users(payload)

def followers_page(uid, key, path, cursor=None, count=20):
    params={'user': str(uid), 'count': str(int(count))}
    if cursor:params['cursor']=cursor
    return parse_page(call(path, key, params))

def fetch_batch(db, job_id, key, path, page_limit=3, unique_limit=50000, count=20, callback=None):
    row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row or row['platform']!='X' or not row['target_uid']:
        raise RapidAPIError('مهمة X غير صالحة.')
    if row['status']=='مكتمل':return 0
    completed=0
    for _ in range(int(page_limit)):
        if db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?',(job_id,)).fetchone()[0]>=int(unique_limit):break
        current=db.execute('SELECT cursor FROM jobs WHERE id=?',(job_id,)).fetchone()['cursor']
        t=time.monotonic()
        try:
            users,nxt=followers_page(row['target_uid'],key,path,current,count)
            if nxt and current and nxt == current:
                raise RapidAPIError('أعاد المزود نفس مؤشر الصفحة؛ أُوقف السحب منعًا للتكرار وتكلفة إضافية.')
            if not users: nxt=None
        except RapidAPIError as exc:
            db.execute('UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?',('متوقف',str(exc),utcnow(),job_id))
            db.commit();add_event(db,job_id,'خطأ',str(exc));raise
        added,dups=save_page(db,job_id,users,nxt,time.monotonic()-t)
        if users and not any(u.get('name') or u.get('username') for u in users):
            add_event(db,job_id,'تنبيه حقول','المزود أرجع معرفات دون أسماء أو يوزرات؛ تتطلب هذه الحسابات بيانات إضافية من مزود المعلومات.')
        completed+=1
        if callback:callback(completed,added,dups)
        if not nxt:break
    return completed
