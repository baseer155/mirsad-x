"""Core persistence and data normalization for Mirsad. No remote calls on import."""
import csv
import io
import re
import sqlite3
import ast
from datetime import datetime, timezone
from pathlib import Path

COUNTRIES = {
    'السعودية': ('السعودية', 'السعوديه', 'saudi arabia', 'ksa', 'riyadh', 'الرياض', 'jeddah', 'جدة', 'مكة', 'mecca', 'dammam', 'الدمام'),
    'الإمارات': ('الإمارات', 'الامارات', 'uae', 'united arab emirates', 'dubai', 'دبي', 'abu dhabi', 'أبوظبي'),
    'الكويت': ('الكويت', 'kuwait'), 'البحرين': ('البحرين', 'bahrain'),
    'قطر': ('قطر', 'qatar', 'doha', 'الدوحة'), 'عُمان': ('عمان', 'عُمان', 'oman', 'muscat', 'مسقط'),
    'مصر': ('مصر', 'egypt', 'cairo', 'القاهرة'), 'اليمن': ('اليمن', 'yemen'),
    'الأردن': ('الأردن', 'الاردن', 'jordan', 'amman'), 'العراق': ('العراق', 'iraq', 'baghdad', 'بغداد'),
    'المغرب': ('المغرب', 'morocco'), 'الولايات المتحدة': ('united states', 'usa', 'new york'),
    'المملكة المتحدة': ('united kingdom', 'london', 'uk'),
}

def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def username_from_input(text):
    value = str(text or '').strip().rstrip('/')
    if value.startswith(('https://x.com/', 'https://twitter.com/', 'http://x.com/', 'http://twitter.com/')):
        value = value.split('/', 3)[-1].split('/')[0].split('?')[0]
    value = value.lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', value):
        raise ValueError('اسم مستخدم X غير صالح')
    return value

def guess_country(location):
    """Heuristic for public, SELF-DECLARED location only, not account-origin country."""
    loc = str(location or '').strip().casefold()
    if not loc:
        return 'غير محددة'
    normalized = re.sub(r'[،|/•]+', ',', loc)
    parts = [p.strip() for p in normalized.split(',')]
    for country, labels in COUNTRIES.items():
        if any(p == label for p in parts for label in labels):
            return country
    return 'غير محددة'

def clean_id(v):
    text = str(v or '').strip().removeprefix("'")
    return text if re.fullmatch(r'[0-9]{1,25}', text) else ''

def safe_public_text(value, max_len=300):
    """Never turn structured objects, numbers or metadata into human names/usernames."""
    if isinstance(value,dict):
        # Common X API 'location': {'location': '...'} envelope.
        for key in ('full_name','display_name','screen_name','username','location','text','value'):
            candidate=value.get(key)
            if isinstance(candidate,str) and candidate.strip():
                return candidate.strip()[:max_len]
        return ''
    if not isinstance(value,str):return ''
    value=value.strip()
    if value.startswith('{') and value.endswith('}'):
        try:return safe_public_text(ast.literal_eval(value),max_len)
        except (SyntaxError,ValueError):return ''
    return value[:max_len]

def safe_username(value):
    value=safe_public_text(value,100).lstrip('@')
    return value if re.fullmatch(r'[A-Za-z0-9_]{1,15}',value) and not value.isdigit() else ''

def safe_name(value):
    value=safe_public_text(value,300)
    # Provider bug: '16' is often a string-length metadata field, not a display name.
    return value if value and not value.isdigit() else ''

def repair_legacy_rows(db,job_id):
    """Mark corrupt old profile fields as missing so subsequent enrichment retries them."""
    rows=db.execute('SELECT uid,name,username,location FROM followers WHERE job_id=?',(job_id,)).fetchall()
    n=0
    for r in rows:
        name=safe_name(r['name']); username=safe_username(r['username']); location=safe_public_text(r['location'])
        if (name,username,location)!=(r['name'] or '',r['username'] or '',r['location'] or ''):
            db.execute('UPDATE followers SET name=?,username=?,location=?,country=? WHERE job_id=? AND uid=?',
                       (name,username,location,guess_country(location),job_id,r['uid']))
            n+=1
    db.commit()
    if n:add_event(db,job_id,'تنظيف قيم غير صحيحة',f'تم تنظيف {n} سجل؛ دون تخمين الأسماء المفقودة')
    return n

def normalize_user(user):
    uid = clean_id(user.get('id') or user.get('id_str') or user.get('user_id') or user.get('uid') or user.get('المعرف الدائم'))
    if not uid:
        return None
    metrics = user.get('public_metrics') or {}
    name = safe_name(user.get('name') or user.get('الاسم'))
    username = safe_username(user.get('username') or user.get('screen_name') or user.get('اليوزر'))
    location = safe_public_text(user.get('location') or user.get('الموقع المعلن'))
    def nullable_bool(key):
        value = user.get(key)
        if value is None or value == '':
            return None
        if isinstance(value, bool):
            return int(value)
        if str(value).lower() in ('1','yes','true','نعم'):return 1
        if str(value).lower() in ('0','no','false','لا'):return 0
        return None
    return {
        'uid':uid,'username':username,'name':name,'location':location,
        'country':guess_country(location),'protected':nullable_bool('protected'),
        'verified':nullable_bool('verified'),
        'followers_count': metrics.get('followers_count',user.get('followers_count')),
        'following_count': metrics.get('following_count',user.get('following_count')),
        'fetched_at':utcnow(),
    }

def connect(path='mirsad.sqlite3'):
    path = str(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS jobs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, target TEXT NOT NULL,
      target_uid TEXT, cursor TEXT, status TEXT NOT NULL DEFAULT 'جاهز',
      started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      pages INTEGER NOT NULL DEFAULT 0, scanned INTEGER NOT NULL DEFAULT 0,
      duplicates INTEGER NOT NULL DEFAULT 0, elapsed_seconds REAL NOT NULL DEFAULT 0,
      error TEXT
    );
    CREATE TABLE IF NOT EXISTS followers(
      job_id INTEGER NOT NULL, uid TEXT NOT NULL, username TEXT, name TEXT,
      location TEXT, country TEXT, protected INTEGER, verified INTEGER,
      followers_count INTEGER, following_count INTEGER, fetched_at TEXT,
      PRIMARY KEY(job_id,uid), FOREIGN KEY(job_id) REFERENCES jobs(id)
    );
    CREATE TABLE IF NOT EXISTS events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
      at TEXT NOT NULL, kind TEXT NOT NULL, scanned INTEGER NOT NULL,
      unique_count INTEGER NOT NULL, details TEXT
    );
    CREATE INDEX IF NOT EXISTS followers_country_idx ON followers(job_id,country);
    ''')
    # Optional provenance fields: never inferred from public-profile location.
    known={row[1] for row in db.execute('PRAGMA table_info(followers)').fetchall()}
    for column in ('account_country','connected_via','suspension_status','about_checked_at'):
        if column not in known:db.execute(f'ALTER TABLE followers ADD COLUMN {column} TEXT')
    db.commit()
    return db

def create_job(db, platform, target, uid=None):
    now = utcnow()
    cur = db.execute('INSERT INTO jobs(platform,target,target_uid,status,started_at,updated_at) VALUES(?,?,?,?,?,?)',
                     (platform,target,uid,'جاهز',now,now))
    db.commit()
    return cur.lastrowid

def add_event(db, job_id, kind, details=''):
    counts = db.execute('SELECT scanned FROM jobs WHERE id=?',(job_id,)).fetchone()
    total = db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?',(job_id,)).fetchone()[0]
    db.execute('INSERT INTO events(job_id,at,kind,scanned,unique_count,details) VALUES(?,?,?,?,?,?)',
               (job_id,utcnow(),kind, counts['scanned'] if counts else 0,total,str(details)[:1000]))
    db.commit()

def save_page(db, job_id, users, next_cursor, seconds):
    data = [u for user in users if (u := normalize_user(user)) is not None]
    before = db.total_changes
    db.executemany('''INSERT OR IGNORE INTO followers(job_id,uid,username,name,location,country,protected,verified,followers_count,following_count,fetched_at)
        VALUES(:job_id,:uid,:username,:name,:location,:country,:protected,:verified,:followers_count,:following_count,:fetched_at)''',
        [{**item,'job_id':job_id} for item in data])
    inserted = db.total_changes-before
    duplicates = len(data)-inserted
    db.execute('''UPDATE jobs SET cursor=?,status=?,updated_at=?,pages=pages+1,scanned=scanned+?,
        duplicates=duplicates+?,elapsed_seconds=elapsed_seconds+?,error=NULL WHERE id=?''',
        (next_cursor,'قابل للاستكمال' if next_cursor else 'مكتمل',utcnow(),len(users),duplicates,seconds,job_id))
    db.commit()
    add_event(db,job_id,'دفعة',f'وارد {len(users)} | جديد {inserted} | مكرر {duplicates} | مدة {seconds:.1f} ثانية')
    return inserted,duplicates

def job_rows(db):
    return db.execute('SELECT * FROM jobs ORDER BY id DESC').fetchall()

def followers_as_dicts(db, job_id):
    return [dict(row) for row in db.execute('SELECT * FROM followers WHERE job_id=? ORDER BY rowid',(job_id,))]

def events_as_dicts(db, job_id):
    return [dict(row) for row in db.execute('SELECT at,kind,scanned,unique_count,details FROM events WHERE job_id=? ORDER BY id',(job_id,))]

def import_csv(db, job_id, content):
    text = content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not any(k in reader.fieldnames for k in ('id','id_str','user_id','uid','المعرف الدائم')):
        raise ValueError('الملف يحتاج عمود id أو user_id أو المعرف الدائم')
    batch=[]; inserted=0
    for row in reader:
        batch.append(row)
        if len(batch) == 1000:
            n,_=save_page(db,job_id,batch,None,0);inserted+=n;batch=[]
    if batch:
        n,_=save_page(db,job_id,batch,None,0);inserted+=n
    db.execute("UPDATE jobs SET status='مكتمل',updated_at=? WHERE id=?",(utcnow(),job_id));db.commit()
    return inserted


def enrich_existing(db, job_id, users):
    """Fill missing profile fields for EXISTING IDs only, never overwrite known data with blanks."""
    repair_legacy_rows(db,job_id)
    changed=0; matched=0
    for raw in users:
        item=normalize_user(raw)
        if not item:continue
        old=db.execute('SELECT uid,name,username,location,protected,verified,followers_count,following_count FROM followers WHERE job_id=? AND uid=?',(job_id,item['uid'])).fetchone()
        if old is None:continue
        matched+=1
        name=item['name'] or old['name'] or ''
        username=item['username'] or old['username'] or ''
        location=item['location'] or old['location'] or ''
        country=guess_country(location)
        protected=item['protected'] if item['protected'] is not None else old['protected']
        verified=item['verified'] if item['verified'] is not None else old['verified']
        followers=item['followers_count'] if item['followers_count'] is not None else old['followers_count']
        following=item['following_count'] if item['following_count'] is not None else old['following_count']
        if any((old[k] or '') != val for k,val in [('name',name),('username',username),('location',location)]):changed+=1
        db.execute("""UPDATE followers SET name=?,username=?,location=?,country=?,protected=?,verified=?,followers_count=?,following_count=?,fetched_at=?
             WHERE job_id=? AND uid=?""",(name,username,location,country,protected,verified,followers,following,utcnow(),job_id,item['uid']))
    db.commit()
    add_event(db,job_id,'إثراء',f'مطابق {matched} | بيانات اسم/يوزر/موقع محسنة {changed}')
    return matched,changed
