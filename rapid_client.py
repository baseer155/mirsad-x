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

def parse_page(payload):
    """Parse observed twitter241 TimelineAddEntries + cursor.bottom response."""
    result = payload.get('result') or {}
    instructions = ((result.get('timeline') or {}).get('instructions') or [])
    users = []
    bottom = ((payload.get('cursor') or {}).get('bottom'))
    for inst in instructions:
        for entry in inst.get('entries') or []:
            content = entry.get('content') or {}
            if content.get('cursorType') == 'Bottom':
                bottom = bottom or content.get('value')
            raw = (((content.get('itemContent') or {}).get('user_results') or {}).get('result') or {})
            if not isinstance(raw, dict):
                continue
            uid = raw.get('rest_id')
            if not uid: continue
            legacy = raw.get('legacy') or {}
            users.append({
                'id': str(uid), 'username': legacy.get('screen_name') or '',
                'name': legacy.get('name') or '',
                'location': legacy.get('location') or '',
                'protected': legacy.get('protected'),
                'verified': raw.get('is_blue_verified') if raw.get('is_blue_verified') is not None else legacy.get('verified'),
                'followers_count': legacy.get('followers_count'),
                'following_count': legacy.get('friends_count'),
            })
    if not users:
        # Fail closed for an unexpected schema, rather than silently marking a
        # task completed when a provider changes its JSON envelope.
        if not instructions and 'result' not in payload:
            raise RapidAPIError('لا تتطابق استجابة المزود مع شكل البيانات المعتمد. راجع JSON قبل الاستكمال.')
    return users, (str(bottom) if bottom else None)

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
        completed+=1
        if callback:callback(completed,added,dups)
        if not nxt:break
    return completed
