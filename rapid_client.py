"""RapidAPI twitter241 adapter. No network requests at import time.

Paths are configurable because RapidAPI providers can change routes.
Never embed API keys in source code.
"""
import time
from urllib.parse import urlsplit
import requests
from core import save_page, add_event, utcnow, safe_public_text, safe_username, safe_name, repair_legacy_rows

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

def _candidate(value,kind):
    if kind=='name':return safe_name(value)
    if kind=='username':return safe_username(value)
    return safe_public_text(value)

def _profile_objects(raw):
    """Follow only X user profile wrappers, not arbitrary unrelated numbers/objects."""
    if not isinstance(raw,dict):return []
    queue=[raw]; visited=set(); found=[]
    while queue and len(visited)<30:
        obj=queue.pop(0)
        if not isinstance(obj,dict) or id(obj) in visited:continue
        visited.add(id(obj));found.append(obj)
        for key in ('result','user_results','user_result','user','data','core','legacy'):
            nested=obj.get(key)
            if isinstance(nested,dict):queue.append(nested)
    return found

def parse_user(raw):
    """Parse X user objects while rejecting metadata masquerading as names."""
    objs=_profile_objects(raw)
    if not objs:return None
    uid=''
    for o in objs:
        val=o.get('rest_id') or o.get('id_str') or o.get('user_id') or o.get('uid')
        if not val and str(o.get('id','')).isdigit():val=o['id']
        if val is not None and str(val).isdigit():uid=str(val);break
    if not uid:return None
    legacy=next((o for o in objs if 'screen_name' in o or ('name' in o and 'followers_count' in o)),{})
    name=username=location=''
    for obj in ([legacy]+objs):
        if not name:name=_candidate(obj.get('name') or obj.get('display_name'),'name')
        if not username:username=_candidate(obj.get('screen_name') or obj.get('username'),'username')
        if not location:location=_candidate(obj.get('location'),'location')
    def get_value(*keys):
        for obj in ([legacy]+objs):
            for k in keys:
                val=obj.get(k)
                if val is not None and not isinstance(val,(dict,list)):
                    return val
        return None
    metrics=next((o.get('public_metrics') for o in objs if isinstance(o.get('public_metrics'),dict)),{})
    followers=get_value('followers_count')
    following=get_value('friends_count','following_count')
    return {'id':uid,'username':username,'name':name,'location':location,
            'protected':get_value('protected'),
            'verified':get_value('is_blue_verified','verified'),
            'followers_count':followers if followers is not None else metrics.get('followers_count'),
            'following_count':following if following is not None else metrics.get('following_count')}

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


# Detailed profile lookup is a separate, potentially billable operation.
# Twttr API (twitter241) exposes GET /get-users with query parameter 'users'.
# Never infer that an account is suspended from a missing profile.

def _walk_user_profiles(payload, max_nodes=100000):
    """Search nested GraphQL and API envelopes for actual profile objects.

    Keep numeric IDs as text. Return only records with an explicit stable numeric ID.
    If a user appears several times, prefer the most complete representation.
    """
    stack=[payload]; seen=0; found={}
    while stack and seen<max_nodes:
        obj=stack.pop();seen+=1
        if isinstance(obj,list):
            stack.extend(obj)
            continue
        if not isinstance(obj,dict):continue
        user=parse_user(obj)
        if user:
            uid=user['id']
            score=sum(bool(user.get(k)) for k in ('name','username','location'))
            prior=found.get(uid)
            if prior is None or score>sum(bool(prior.get(k)) for k in ('name','username','location')):
                found[uid]=user
        for k,v in obj.items():
            if isinstance(v,(dict,list)) and k not in ('entities','description','affiliates_highlighted_label'):
                stack.append(v)
    return list(found.values())

def lookup_profiles_by_ids(ids, key, path='/get-users'):
    """Lookup public profiles by numeric user IDs using the provider's own endpoint.

    Pass no more than a small batch to avoid an undocumented size limit.
    No guessing or fallback to unrelated providers.
    """
    ids=[str(uid) for uid in ids if str(uid).isdigit()]
    if not ids:return []
    payload=call(path,key,{'users':','.join(ids)})
    found=_walk_user_profiles(payload)
    allowed=set(ids)
    return [u for u in found if u['id'] in allowed]

def enrich_missing_job(db,job_id,key,path='/get-users',batch_size=20,max_batches=5,callback=None):
    """Complete missing name, username, or location for previously saved IDs.

    Returns numbers processed, matched, changed. Also logs unsuccessful lookups
    without inventing empty profile fields or rewriting identifiers.
    """
    from core import enrich_existing
    batch_size=max(1,min(int(batch_size),100))
    max_batches=max(1,min(int(max_batches),100))
    repair_legacy_rows(db,job_id)
    missing=db.execute("SELECT uid FROM followers WHERE job_id=? AND " +
        "(COALESCE(name,'')='' OR COALESCE(username,'')='') ORDER BY rowid",(job_id,)).fetchall()
    total_queried=matched=changed=0
    for k in range(0,min(len(missing),batch_size*max_batches),batch_size):
        ids=[str(x['uid']) for x in missing[k:k+batch_size]]
        try:
            enriched=lookup_profiles_by_ids(ids,key,path)
        except RapidAPIError as e:
            add_event(db,job_id,'فشل إثراء',f'توقف استكمال معلومات {len(ids)} حساب: {e}')
            raise
        a,b=enrich_existing(db,job_id,enriched)
        total_queried+=len(ids);matched+=a;changed+=b
        if callback:callback(total_queried,matched,changed)
    add_event(db,job_id,'استكمال ملف شخصي',f'تم الاستعلام عن {total_queried} معرف؛ مطابقة {matched}؛ تحديث {changed}')
    return total_queried,matched,changed


def diagnose_lookup(ids, key, path='/get-users'):
    """One deliberately triggered request; returns structural diagnostics, no secrets/raw PII."""
    ids=[str(x) for x in ids if str(x).isdigit()][:3]
    payload=call(path,key,{'users':','.join(ids)})
    results=_walk_user_profiles(payload)
    return {'requested':len(ids),'matched':len([x for x in results if x['id'] in ids]),
            'root_fields':list(payload.keys())[:20],
            'profile_with_name':sum(bool(x['name']) for x in results),
            'profile_with_username':sum(bool(x['username']) for x in results)}

def about_information(payload):
    """Use explicit origin/status indicators only; never infer suspension or connection."""
    keys_country=('account_based_in','account_country','country_of_account','country_of_origin')
    keys_connected=('connected_via','connected_through','app_store')
    keys_status=('suspension_status','account_status','suspended')
    found={'account_country':None,'connected_via':None,'suspension_status':None}
    stack=[payload]
    while stack:
        obj=stack.pop()
        if isinstance(obj,list):stack.extend(obj);continue
        if not isinstance(obj,dict):continue
        for field,keys in (('account_country',keys_country),('connected_via',keys_connected),('suspension_status',keys_status)):
            if found[field] is None:
                for key in keys:
                    val=obj.get(key)
                    if key=='suspended' and isinstance(val,bool):val='موقوف' if val else 'غير موقوف حسب المصدر'
                    if isinstance(val,str) and val.strip():found[field]=val.strip()[:150];break
        for v in obj.values():
            if isinstance(v,(dict,list)):stack.append(v)
    return found

def lookup_about(uid,key,path):
    # Optional third-party route; user must confirm required query field and semantics.
    return about_information(call(path,key,{'user':str(uid)}))
