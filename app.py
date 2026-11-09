"""مِرصاد | Independent Arabic multi-platform monitoring dashboard."""
import hmac
from contextlib import closing
import json
import os
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

from core import (connect,create_job,add_event,followers_as_dicts,events_as_dicts,
                  import_csv,job_rows,username_from_input,utcnow,enrich_existing,repair_legacy_rows)
from exporter import make_excel, make_group_excel
from report_export import make_word, make_powerpoint
from filtering import exportable_rows
from x_client import XAPIError,lookup_user,fetch_batch as fetch_official
from rapid_client import RapidAPIError, fetch_batch as fetch_rapid, validate_path, extract_users, enrich_missing_job, diagnose_lookup, lookup_about, lookup_about_diagnostic, enrich_about_job, lookup_profiles_by_ids
from auto_enrich import policy as about_policy, set_policy as save_about_policy, stats as about_stats, run_batch as run_auto_about, reset_interrupted, requeue_missing_connection, connection_stats
from auto_runner import step as auto_step, snapshot as auto_snapshot
from multi_accounts import (parse_targets, resolve_targets, create_group, get_groups, get_group, members as group_members,
                            resume as resume_group, set_status as set_group_status, tick as group_tick)
from performance import configure as configure_speed, metrics as speed_metrics
from readiness import inspect_job, inspect_group
from report_fields import safe_report_name, account_links, account_status, connected_via, elapsed_metrics, fmt_duration

st.set_page_config(page_title='مِرصاد | ذكاء المنصات الاجتماعية',page_icon='🛰️',layout='wide',initial_sidebar_state='expanded')
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;600;700;800&display=swap');
html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {direction:rtl; text-align:right; font-family:'Tajawal',Tahoma,Arial,sans-serif;}
/* Streamlit Sidebar and Main are laid out from right to left */
[data-testid="stAppViewContainer"] {flex-direction:row-reverse !important;}
section[data-testid="stSidebar"] {right:0 !important;left:auto !important;border-right:0 !important;border-left:1px solid #dbe4eb !important;background:#f6f9fc !important;}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap:.55rem;}
section[data-testid="stSidebar"] label {font-family:inherit;}
section[data-testid="stSidebar"] [role="radiogroup"] {display:flex;flex-direction:column;gap:7px;}
section[data-testid="stSidebar"] [role="radiogroup"] label {display:flex;align-items:center;justify-content:flex-start;border:1px solid #e2e9ef;border-radius:11px;background:#ffffff;padding:12px 10px;margin:0;min-height:43px;transition:background .2s,border .2s;}
section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#e2f3f2;border-color:#0f9188;color:#0b564f;font-weight:800;}
section[data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#edf7f7;}
[data-testid="stMainBlockContainer"] {max-width:1400px;padding:1.5rem 2rem 3rem;}
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input {direction:ltr;text-align:left;}
[data-testid="stMetricValue"] {direction:ltr;text-align:right;font-family:'Tajawal',sans-serif;}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {direction:rtl;}
h1,h2,h3,h4,p,button,span {font-family:'Tajawal',Tahoma,Arial,sans-serif;}
.hero{padding:28px 32px;background:linear-gradient(115deg,#0c2836,#16505b);color:white;border-radius:18px;margin-bottom:20px;box-shadow:0 8px 24px #15384b14;}
.hero h1{font-size:2.3rem;margin:0;color:white;font-weight:800;}.hero p{margin:8px 0 0;color:#d1ecea;font-size:1rem;}
.small{color:#6b7280;font-size:13px}
.section-title{border-right:4px solid #159a8c;padding-right:12px;margin:14px 0 18px;font-weight:800;color:#143546}
section[data-testid="stSidebar"] h1 {font-size:1.35rem;}
div[data-testid="stMetric"] {background:#fff;border:1px solid #e3eaf0;border-radius:13px;padding:13px 17px;}
div[data-testid="stAlert"] {border-radius:11px;}
[data-testid="stMain"] {direction:rtl;text-align:right;}
[data-testid="stExpander"] {border-radius:14px;border-color:#e1e9ef;}
[data-testid="stButton"] button, [data-testid="stDownloadButton"] button {border-radius:11px;font-weight:700;}
[data-testid="stMain"] label {text-align:right;}
[data-testid="stMain"] textarea {direction:ltr;text-align:left;}
[data-testid="stMain"] h2,[data-testid="stMain"] h3 {letter-spacing:0;line-height:1.5;}

@media(max-width:800px){[data-testid="stMainBlockContainer"]{padding:1rem}.hero{padding:17px}.hero h1{font-size:1.55rem}}

/* Phase 3 — simplified, right-to-left operating interface */
:root{--mirsad-ink:#163547;--mirsad-teal:#087e79;--mirsad-edge:#e4edf0;--mirsad-bg:#f8fafb}
[data-testid="stApp"],[data-testid="stMain"]{background:var(--mirsad-bg)}
[data-testid="stMainBlockContainer"]{max-width:1340px!important;padding:1.25rem 2.25rem 4rem!important}
[data-testid="stSidebar"]{background:#fff!important}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.55rem}
[data-testid="stSidebar"] [role="radiogroup"] label{box-shadow:none!important;border:0!important;border-right:3px solid transparent!important;background:transparent!important;border-radius:7px!important;padding:10px 12px!important}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked){background:#eaf7f4!important;border-right:3px solid var(--mirsad-teal)!important;color:#065f5b!important}
.hero{background:#fff!important;color:var(--mirsad-ink)!important;border:1px solid var(--mirsad-edge);box-shadow:none!important;padding:18px 24px!important;border-radius:15px!important;margin-bottom:17px!important}
.hero h1{font-size:1.75rem!important;color:var(--mirsad-ink)!important}
.hero p{color:#667985!important;font-size:.95rem!important;margin-top:3px!important}
.hero-brow{color:var(--mirsad-teal);font-size:.75rem;font-weight:800;letter-spacing:.02em;margin-bottom:2px}
.step-strip{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 20px}
.step-strip span{background:#fff;border:1px solid #dfe9ed;color:#496777;padding:8px 14px;border-radius:999px;font-size:.88rem;font-weight:700}
[data-testid="stMain"] [role="radiogroup"]{justify-content:flex-start;gap:12px;direction:rtl}
[data-testid="stMain"] [role="radiogroup"] label{background:#fff;border:1px solid #e0e9ed;border-radius:12px;padding:8px 18px}
[data-testid="stMain"] [role="radiogroup"] label:has(input:checked){border-color:#0d8b83;background:#eaf7f4}
[data-testid="stMetric"]{background:#fff!important;border:1px solid var(--mirsad-edge)!important;border-radius:14px!important;padding:16px!important;min-width:0!important;overflow:visible!important}
[data-testid="stMetricLabel"]{color:#58717d!important}
[data-testid="stMetricValue"]{font-size:clamp(1.25rem,2vw,2rem)!important;white-space:normal!important;overflow-wrap:anywhere!important;line-height:1.28!important}
[data-testid="stVerticalBlockBorderWrapper"]:has([data-testid="stExpander"]){border-radius:14px}
[data-testid="stExpander"]{background:#fff;border:1px solid var(--mirsad-edge);border-radius:14px!important;overflow:hidden}
[data-testid="stExpander"] summary{font-weight:700;color:var(--mirsad-ink)}
[data-testid="stButton"] button,[data-testid="stDownloadButton"] button,[data-testid="stFormSubmitButton"] button{border-radius:10px!important;min-height:42px!important}
[data-testid="stButton"] button[kind="primary"],[data-testid="stFormSubmitButton"] button[kind="primary"]{background:#087e79!important;border-color:#087e79!important;color:#fff!important}
[data-testid="stMain"] h2{font-size:1.5rem!important;color:#193d4d;line-height:1.5!important}
[data-testid="stMain"] h3{font-size:1.15rem!important;color:#193d4d}
[data-testid="stMain"] [data-testid="stForm"]{border:1px solid var(--mirsad-edge);background:#fff;border-radius:14px;padding:16px}
[data-testid="stMain"] label{direction:rtl!important;text-align:right!important}
[data-testid="stMain"] input[type="text"],[data-testid="stMain"] textarea{border-radius:9px!important}
[data-testid="stMain"] textarea{unicode-bidi:plaintext;direction:ltr!important;text-align:left!important}
[data-testid="stDataFrame"]{background:white;border-radius:12px;overflow:hidden}
@media(max-width:820px){[data-testid="stMainBlockContainer"]{padding:1rem .85rem 3rem!important}.hero{padding:15px!important}.step-strip span{padding:6px 9px;font-size:.78rem}[data-testid="stMetric"]{padding:10px!important}}
</style>""",unsafe_allow_html=True)

def secret(name):
    try:return str(st.secrets.get(name,'')).strip()
    except Exception:return str(os.getenv(name,'')).strip()

def signed_in():
    password=secret('ADMIN_PASSWORD')
    if not password:return False
    if st.session_state.get('authenticated') is True:return True
    with st.sidebar.form('login'):
        value=st.text_input('كلمة مرور المسؤول',type='password')
        ok=st.form_submit_button('دخول المسؤول',use_container_width=True)
    if ok:
        if hmac.compare_digest(value,password):
            st.session_state['authenticated']=True
            st.rerun()
        else:st.sidebar.error('كلمة المرور غير صحيحة')
    return False

st.markdown('<div class="hero"><div class="hero-brow">لوحة العمليات الرقمية</div><h1>مِرصاد</h1><p>إدارة مهام السحب والتحليل والتقارير</p></div>',unsafe_allow_html=True)
st.sidebar.title('مِرصاد | لوحة التحكم')
admin=signed_in()
if not admin:
    st.subheader('المرصد الرقمي متعدد المنصات')
    c1,c2,c3=st.columns(3)
    c1.metric('منصة X','متاحة عند ربط API')
    c2.metric('تيك توك','استيراد ملفات')
    c3.metric('إنستغرام','استيراد ملفات')
    st.info('الموقع عام، لكن أدوات استخراج البيانات والملفات محمية بالدخول. لا تُعرض أي مفاتيح API للزوار.')
    st.markdown('**المزايا:** معرّفات دائمة بصيغة نص، تنظيف التكرارات، تحليل دولة الحساب من «حول الحساب»، مخططات وتصدير Excel عربي بخمس أوراق.')
    st.caption('دولة الحساب تعتمد فقط على صفحة «حول الحساب» عند توفر بياناتها من مزود الخدمة، ولا تعبّر بالضرورة عن الجنسية أو الموقع الحالي.')
    if not secret('ADMIN_PASSWORD'):st.warning('لم تُضبط ADMIN_PASSWORD في إعدادات أسرار الخادم، ولذلك لوحة الإدارة معطلة حمايةً للبيانات.')
    st.stop()

with st.sidebar:
    st.caption('جلسة المسؤول نشطة')
    if st.button('تسجيل الخروج',use_container_width=True):
        st.session_state['authenticated']=False;st.rerun()


db_path=secret('MIRSAD_DB') or 'mirsad.sqlite3'
try:db=connect(db_path)
except Exception as e:
    st.error('تعذّر فتح قاعدة البيانات. تحقق من إعدادات التخزين.');st.stop()
token=secret('X_BEARER_TOKEN')
rapid_key=secret('RAPIDAPI_KEY')
rapid_path=secret('RAPIDAPI_FOLLOWERS_PATH')
rapid_profiles_path=secret('RAPIDAPI_PROFILES_PATH') or '/get-users'
rapid_about_path=secret('RAPIDAPI_ABOUT_PATH') or '/about-account'
api_mode='RapidAPI' if rapid_key else 'X الرسمية'
configured=bool(rapid_key and rapid_path) if rapid_key else bool(token)
external_worker=secret('MIRSAD_EXTERNAL_WORKER').lower() in ('1','true','yes')

# Persist provider follower totals independently from the user-defined page safety cap.
# A failed lookup is recorded to prevent billable retries on every Streamlit rerun.
def _ensure_single_target_counts(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS single_target_counts (
        job_id INTEGER PRIMARY KEY, follower_total INTEGER, checked_at TEXT NOT NULL,
        error TEXT, FOREIGN KEY(job_id) REFERENCES jobs(id))""")
    conn.commit()

def _read_single_target_count(conn, job_id, target_uid, api_key, profiles_path, *, retry=False):
    _ensure_single_target_counts(conn)
    row=conn.execute('SELECT follower_total,checked_at,error FROM single_target_counts WHERE job_id=?',(job_id,)).fetchone()
    if row is not None and not retry:
        return dict(row)
    try:
        records=lookup_profiles_by_ids([str(target_uid)],api_key,profiles_path)
        match=next((x for x in records if str(x.get('id'))==str(target_uid)),None)
        value=match.get('followers_count') if match else None
        count=int(value) if value is not None and str(value).strip().isdigit() else None
        error=None if count is not None else 'مزود الخدمة لم يرجع عدد المتابعين'
    except Exception as exc:
        count=None
        error='تعذر استعلام مزود الخدمة: '+type(exc).__name__
    conn.execute("""INSERT INTO single_target_counts(job_id,follower_total,checked_at,error)
                  VALUES(?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
                  follower_total=excluded.follower_total,checked_at=excluded.checked_at,error=excluded.error""",
                 (job_id,count,utcnow(),error))
    conn.commit()
    return {'follower_total':count,'checked_at':utcnow(),'error':error}

# قائمة رئيسية عمودية على يمين الموقع — كل قسم مستقل
with st.sidebar:
    st.markdown('### القائمة الرئيسية')
    page = st.radio('الأقسام', ['🏠 الرئيسية', '🔎 سحب متابعي X', '📥 استيراد البيانات', '📊 التحليلات والتقارير', '⚙️ دليل المنصات'],
        label_visibility='collapsed', key='mirsad_section')
    

if page == '🏠 الرئيسية':
    st.markdown('<h2 class="section-title">لوحة التحكم الرئيسية</h2>', unsafe_allow_html=True)
    all_jobs=job_rows(db)
    all_count=db.execute('SELECT COUNT(*) FROM followers').fetchone()[0]
    all_events=db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    k1,k2,k3=st.columns(3)
    k1.metric('إجمالي الحسابات المحفوظة',f'{all_count:,}')
    k2.metric('المهام المنشأة',f'{len(all_jobs):,}')
    k3.metric('أحداث سجل التشغيل',f'{all_events:,}')
    st.markdown('#### المهام الحديثة')
    if all_jobs:
        st.dataframe(pd.DataFrame([dict(x) for x in all_jobs[:15]])[['id','platform','target','status','pages','scanned','updated_at']],hide_index=True,use_container_width=True)
    else:
        st.info('لم تبدأ أي مهمة بعد. استخدم قائمة «سحب متابعي X» على اليمين.')
    st.caption('تقدم السحب الفعلي يعتمد على الصفحات التي يعيدها المزود. بلوغ نهاية الصفحات لا يعني الوصول إلى العدد المستهدف بالضرورة.')

if page == '🔎 سحب متابعي X':
    st.markdown('## إدارة سحب المتابعين')
    st.caption('أنشئ مهمة أو تابع مهمة محفوظة، ثم راجع النتائج في صفحة التقارير.')
    st.markdown('<div class="step-strip"><span>١ · إنشاء المهمة</span><span>٢ · السحب والإثراء</span><span>٣ · اعتماد التقرير</span></div>',unsafe_allow_html=True)
    mode=st.radio('نوع المهمة', ['مجموعة حسابات','حساب واحد'], horizontal=True, key='v17_phase3_mode')
    if mode == 'مجموعة حسابات':
        with st.expander('إنشاء مجموعة جديدة', expanded=True):
            with st.form('multi_target_create', clear_on_submit=False):
                multi_name=st.text_input('اسم المجموعة', value='مجموعة متابعة X')
                raw_targets=st.text_area('حساب واحد في كل سطر — اليوزر أو الرابط فقط',
                    placeholder='@account_one\nhttps://x.com/account_two\n@account_three',height=145)
                st.caption('يقرأ النظام المعرف الدائم وعدد المتابعين تلقائيًا، ثم يتحقق من الحسابات قبل إنشاء المهمة. قد تُحتسب طلبات تحقق مدفوعة.')
                with st.expander("إعدادات السحب والإثراء المتقدمة", expanded=False):
                    aa,bb,cc=st.columns(3)
                    per_target_goal=1000000
                    aa.info('العدد المستهدف يُقرأ من RapidAPI تلقائيًا')
                    per_target_pages=bb.number_input('سقف الصفحات لكل حساب',min_value=1,max_value=100000,value=3000,step=250)
                    multi_page_size=cc.number_input('متابعون مطلوبون لكل طلب',min_value=1,max_value=200,value=20,step=10)
                    st.caption('إعدادات التشغيل المتقدمة: اترك القيم الافتراضية ما لم تستدعِ الباقة غير ذلك.')
                    dd,ee,ff=st.columns(3)
                    pages_tick=dd.number_input('صفحات في دورة الحساب',min_value=1,max_value=5,value=1)
                    about_tick=ee.number_input('فحوص About في الدورة',min_value=1,max_value=50,value=10)
                    ff.markdown('**إثراء About آلي**')
                    ff.caption('تُضاف حسابات جديدة إلى طابور الفحص مع كل دفعة')
                    about_enabled=st.checkbox('فحص «حول الحساب» تلقائيًا للمتابعين',value=True)
                    st.caption('فحوص About قد تستهلك طلبات إضافية من الاشتراك.')
                submitted=st.form_submit_button('إنشاء مجموعة الحسابات',type='primary',disabled=not bool(rapid_key and rapid_path))
            if submitted:
                items,problems=parse_targets(raw_targets)
                if problems:
                    st.error('لم تُنشأ المجموعة؛ صحّح الإدخال:\n'+'\n'.join('• '+err for err in problems[:15]))
                else:
                    try:
                        verified,lookup_errors=resolve_targets(items,rapid_key,secret('RAPIDAPI_USER_PATH') or '/user')
                        if lookup_errors:
                            st.error('تعذر التحقق من جميع الحسابات؛ لم تُنشأ المجموعة: ' + ' | '.join(lookup_errors[:12]))
                            st.stop()
                        gid=create_group(db,multi_name,verified,goal=per_target_goal,page_cap=per_target_pages,
                           page_size=multi_page_size,pages_per_tick=pages_tick,about_per_tick=about_tick,
                           about_enabled=about_enabled)
                        # One deliberate submit starts the group; avoid a second manual click.
                        resume_group(db,gid)
                        st.session_state['selected_group']=gid
                        st.success(f'أُنشئت المجموعة #{gid} وفيها {len(items)} حسابات. بدأ السحب والإثراء تلقائيًا.')
                        st.rerun()
                    except Exception as exc:
                        st.error(f'لم يتم إنشاء المجموعة: {type(exc).__name__}')
        groups=get_groups(db)
        if groups:
            names={g['id']:f"#{g['id']} | {g['name']} | {g['status']}" for g in groups}
            group_id=st.selectbox('📁 إدارة مجموعة الحسابات',list(names),format_func=lambda k:names[k],
                      index=next((i for i,k in enumerate(names) if k==st.session_state.get('selected_group')),0),key='multi_group_selected')
            group=get_group(db,group_id)
            gr=group_members(db,group_id)
            group_quality=inspect_group(db,group_id)
            if group_quality['approved']:
                st.success('✅ تم الانتهاء من المهمة — نتائج المجموعة مؤهلة')
            else:
                st.info('حالة اعتماد المجموعة: '+group_quality['reason'])
            total_unique=sum(r['unique_count'] for r in gr)
            total_country=sum(r['with_country'] for r in gr)
            gm1,gm2,gm3,gm4=st.columns(4)
            gm1.metric('الحسابات المستهدفة',len(gr))
            gm2.metric('المتابعون المسحوبون',f'{total_unique:,}')
            gm3.metric('دولة من About',f'{total_country:,}')
            gm4.metric('المهام المكتملة',f"{sum(r['state']=='مكتمل' for r in gr):,} / {len(gr)}")
            known_goal=sum(min(group['goal_per_target'], r['follower_total']) for r in gr if r['follower_total'] is not None)
            unknown=sum(r['follower_total'] is None for r in gr)
            if not unknown and known_goal>0:
                st.progress(min(1.,total_unique/known_goal),text='التقدم بحسب عدد المتابعين المؤكد من المصدر')
            else:
                st.caption('نسبة الإنجاز الإجمالية لا تُحسب قبل معرفة أعداد جميع الحسابات؛ العدد المسحوب يظهر مباشرة.')
            dataframe=pd.DataFrame([{'الحساب':'@'+r['target'],'المعرف':r['target_uid'],
                       'المتابعون':r['unique_count'],'العدد المعلن لدى المصدر':r['follower_total'] if r['follower_total'] is not None else 'غير متاح', 'دولة من About':r['with_country'],
                       'الصفحات':r['pages'],'حالة المجموعة':r['state'],
                       'الوقت (ث)':round(r['elapsed_seconds'],1),'ملاحظة':r['message'] or ''} for r in gr])
            st.dataframe(dataframe,hide_index=True,use_container_width=True)
            done_about=db.execute('''SELECT COUNT(*) FROM about_attempts a
                JOIN target_group_members m ON m.job_id=a.job_id
                WHERE m.group_id=? AND a.state='done' ''',(group_id,)).fetchone()[0]
            st.progress(min(1.,done_about/max(1,total_unique)),
                text=f'إثراء About: {done_about:,} / {total_unique:,} متابعًا تم فحصه بنجاح')
            st.caption('يستمر فحص About تلقائيًا مع اكتشاف المتابعين؛ انتهاء السحب وحده لا يعني اعتماد النتائج.')
            with st.expander('⚡ ضبط سرعة المجموعة',expanded=False):
                speed_data=speed_metrics(db,group_id)
                rate=st.number_input('سقف طلبات RapidAPI في الدقيقة',min_value=1,max_value=120,value=int(speed_data['rpm_limit']),key=f'rpm_{group_id}')
                size=st.number_input('عدد المتابعين المطلوب في الصفحة',min_value=1,max_value=200,value=100,key=f'size_{group_id}')
                if st.button('حفظ سرعة المجموعة',key=f'speed_save_{group_id}'):
                    configure_speed(db,group_id,rpm=rate,page_size=size)
                    st.success('تم تحديث سرعة المجموعة')
                st.caption(f"طلبات متاحة في الدقيقة الحالية: {speed_data['remaining_window']} | انتظار: {speed_data['wait_seconds']} ثانية")
            st.download_button(('⬇️ Excel — نتائج المجموعة النهائية' if group_quality['approved'] else '⬇️ Excel — نتائج المجموعة الأولية'),data=make_group_excel(db,group_id),
                file_name=f'تحليل_المجموعة_{group_id}.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                key=f'group_overlap_{group_id}',use_container_width=True)
    
            # The persisted group status is the source of truth. Streamlit session
            # state is transient and must never gate an already-running group.
            is_running = group['status']=='قيد التشغيل'
            col_start,col_stop=st.columns(2)
            if col_start.button('▶ بدء / استكمال المجموعة' if not is_running else '✓ السحب التلقائي يعمل',
                      key=f'multi_start_{group_id}', disabled=is_running or
                      not bool(rapid_key and rapid_path) or all(r['state']=='مكتمل' for r in gr)):
                resume_group(db,group_id)
                st.rerun()
            if col_stop.button('⏸ إيقاف المجموعة وحفظ التقدم',key=f'multi_stop_{group_id}',disabled=not is_running):
                set_group_status(db,group_id,'متوقف')
                st.rerun()
            if is_running and not external_worker:
                st.caption('التشغيل مستمر تلقائيًا ما دامت هذه الصفحة وجلسة Streamlit فعالتين؛ لا تضغط تشغيل بعد كل دفعة.')
            elif is_running and external_worker:
                st.caption('تُنفذ المهام بواسطة العامل الخارجي؛ يمكنك متابعة النتائج هنا.')
            elif not group_quality['approved']:
                failed=[r for r in gr if r['state'] in ('متوقف','خطأ')]
                if failed:
                    st.warning('توقفت بعض الحسابات. راجع الأسباب في الجدول قبل اختيار «بدء / استكمال المجموعة».')
            @st.fragment(run_every='2s')
            def multi_group_panel(gid):
                # Connect afresh on every fragment tick to avoid cross-thread SQLite errors.
                with closing(connect(db_path)) as _fragment_db:
                    g=get_group(_fragment_db,gid)
                    if g is None:
                        st.error('المجموعة غير موجودة في قاعدة البيانات الحالية.')
                        return
                    rs=group_members(_fragment_db,gid)
                    st.caption(f"الحالة: {g['status']} | قيد المعالجة: {sum(x['state']=='جاهز' for x in rs)} | متوقفة: {sum(x['state'] in ('متوقف','خطأ') for x in rs)}")
                    # Live, fragment-scoped progress. The table above is a static
                    # snapshot until the next full page refresh.
                    fresh_count=sum(int(x['unique_count']) for x in rs)
                    finished_count=sum(x['state']=='مكتمل' for x in rs)
                    st.caption(f'متابعون مسحوبون حتى الآن: {fresh_count:,} | مهام مكتملة: {finished_count} من {len(rs)}')
                    if g['status']!='قيد التشغيل':
                        if inspect_group(_fragment_db,gid)['approved']:
                            st.success('✅ تم الانتهاء من المهمة')
                        else:
                            st.info('المجموعة متوقفة أو تنتظر الاستئناف. لا توجد طلبات API جارية.')
                        return
                    if external_worker:
                        st.info('العامل الخارجي مسؤول عن التنفيذ؛ لا تُجرى طلبات من لوحة المتصفح.')
                        return
                    try:
                        answer=group_tick(_fragment_db,gid,rapid_key=rapid_key,followers_path=rapid_path,
                                          about_path=rapid_about_path,profiles_path=rapid_profiles_path)
                    except Exception as exc:
                        # Stop on unexpected local errors instead of silently requiring clicks.
                        set_group_status(_fragment_db,gid,'متوقف')
                        st.error(f'توقف التشغيل بسبب خطأ محلي ({type(exc).__name__}). راجع سجل التطبيق.')
                        return
                    if answer.get('target'):
                        st.caption(f"آخر حساب: @{answer['target']} | متابعون جدد: {answer.get('new_unique',0)} | فحوص About: {answer.get('about_attempted',0)}")
                    if answer.get('state')=='rate_limited':
                        wait=speed_metrics(_fragment_db,gid)['wait_seconds']
                        st.info(f'انتظار حد طلبات RapidAPI: نحو {wait} ثانية. ستُستأنف المعالجة تلقائيًا.')
                    elif answer.get('state')=='finished':
                        if inspect_group(_fragment_db,gid)['approved']:
                            st.success('✅ تم الانتهاء من المهمة')
                        else:
                            st.warning('انتهت المهام المتاحة لكن بعضها لم يجتز شروط الاعتماد. راجع ملاحظات الحسابات.')
                    elif answer.get('state') in ('error','paused'):
                        st.warning('توقف هذا الحساب: '+str(answer.get('message',''))[:180]+' — ستُعالج الحسابات الأخرى إذا كانت جاهزة.')
                    else:
                        st.caption('يُحفظ التقدم تلقائيًا بعد كل دفعة، وتبدأ الدفعة التالية دون تدخل.')
            multi_group_panel(group_id)
    else:
        st.subheader('سحب حساب واحد')
        st.caption('أنشئ المهمة باستخدام اليوزر والمعرف الرقمي ثم تابع تقدمها.')
        user_raw=st.text_input('اليوزر أو رابط حساب X',placeholder='https://x.com/example')
        with st.expander('إعدادات الدفعة المتقدمة', expanded=False):
            page_limit=st.slider('أقصى صفحات لكل ضغطة',1,20,3)
            st.caption('عدد المتابعين يُقرأ تلقائيًا عند توفره؛ هذا الحد يتحكم في عدد صفحات كل دفعة.')
        # Provider count is resolved after a job exists; safe high cap is a fallback,
        # not a claim that the account has this many followers.
        target_limit=1000000
        if rapid_key and not rapid_path:st.warning('مفتاح RapidAPI محفوظ، لكن يجب إضافة RAPIDAPI_FOLLOWERS_PATH من توثيق المسار الفعلي إلى Secrets.')
        elif not configured:st.warning('لا يوجد اتصال مكتمل. ضبط مفتاح RapidAPI ومساره، أو X_BEARER_TOKEN، لتفعيل السحب.')
        if rapid_key:
            target_uid_input=st.text_input('المعرف الرقمي الدائم للحساب المستهدف (User ID)',placeholder='2455740283')
            with st.expander('خيارات RapidAPI المتقدمة', expanded=False):
                page_count=st.number_input('الحجم المطلوب في كل طلب RapidAPI',min_value=1,max_value=200,value=20,step=10)
        else:
            target_uid_input=''
            page_count=20
        if st.button('إنشاء مهمة سحب جديدة',type='primary',disabled=not bool(configured and (target_uid_input.strip().isdigit() if rapid_key else user_raw.strip()))):
            try:
                target=username_from_input(user_raw) if user_raw.strip() else target_uid_input.strip()
                if rapid_key:
                    validate_path(rapid_path)
                    if not target_uid_input.strip().isdigit():raise ValueError('أدخل User ID الرقمي للحساب المستهدف.')
                    uid=target_uid_input.strip()
                else:
                    with st.spinner('التحقق من الحساب عبر X API...'):
                        user=lookup_user(target,token)
                    uid=str(user['id'])
                job_id=create_job(db,'X',target,uid)
                add_event(db,job_id,'إنشاء',f"تم تحديد الحساب @{target}")
                st.session_state['selected_job']=job_id
                st.success(f'تم إنشاء المهمة #{job_id} للحساب @{target}، ويمكن الآن تنفيذ دفعات السحب أدناه.')
            except (ValueError,XAPIError) as e:st.error(str(e))
        jobs=[r for r in job_rows(db) if r['platform']=='X' and r['target_uid']]
        if jobs:
            choices={r['id']:f"#{r['id']} | @{r['target']} | {r['status']}" for r in jobs}
            selected=st.selectbox('مهمة موجودة للاستكمال',options=list(choices),format_func=lambda x:choices[x],index=next((i for i,k in enumerate(choices) if k==st.session_state.get('selected_job')),0))
            job=db.execute('SELECT * FROM jobs WHERE id=?',(selected,)).fetchone()
            unique=db.execute('SELECT COUNT(*) FROM followers WHERE job_id=?',(selected,)).fetchone()[0]
            if rapid_key:
                profile_count=_read_single_target_count(db,selected,job['target_uid'],rapid_key,rapid_profiles_path)
                if st.button('↻ إعادة قراءة عدد المتابعين من المصدر',key=f'refresh_total_{selected}',help='يُرسل طلبًا إضافيًا وقد يحتسب ضمن باقة RapidAPI'):
                    profile_count=_read_single_target_count(db,selected,job['target_uid'],rapid_key,rapid_profiles_path,retry=True)
                provider_total=profile_count.get('follower_total')
                if provider_total is not None:
                    target_limit=max(1,int(provider_total))
                    st.metric('عدد المتابعين وفق بيانات X',f'{target_limit:,}')
                    if unique>target_limit:
                        st.caption('المستخرج أكبر من العدد المعلن؛ قد تكون بيانات المصدر متأخرة أو تغير عدد المتابعين.')
                else:
                    st.info('عدد متابعي الحساب غير متاح من RapidAPI؛ يستمر السحب وفق نهاية الصفحات وسقف الحماية، دون إدخال عدد يدوي.')
            else:
                st.caption('يحدد السحب التلقائي نهاية القائمة المتاحة من API الرسمي إن أمكن.')
            a,b,c,d=st.columns(4)
            a.metric('حسابات فريدة',f'{unique:,}')
            b.metric('سجلات تمت معالجتها',f"{job['scanned']:,}")
            c.metric('تكرارات مستبعدة',f"{job['duplicates']:,}")
            d.metric('وقت API المسجل',f"{job['elapsed_seconds']:.1f} ث")
            st.progress(min(1.,unique/target_limit),text=(f'التقدم نحو عدد المتابعين المعلن: {target_limit:,}' if rapid_key and provider_total is not None else 'المتابعون المستخرجون — العدد الإجمالي غير متاح'))
            if job['error']:st.warning('آخر خطأ: '+job['error'])
            if rapid_key:
                st.markdown('#### 🌍 إثراء «حول الحساب» التلقائي — مستقل عن السحب')
                current_policy=about_policy(db,selected)
                with st.expander('ميزانية فحوص About وإعداداتها',expanded=False):
                    enabled=st.checkbox('استكمال معلومات حول الحساب تلقائيًا عقب سحب المتابعين',value=bool(current_policy['enabled']),key=f'auto_enabled_{selected}')
                    st.caption('حجم الدفعة: 50 حسابًا كحد أقصى عند كل تشغيل. إعادة المحاولة مفعلة للأخطاء المؤقتة فقط، مع انتظار ساعة أو أكثر حسب عدد المحاولات.')
                    cap=st.number_input('السقف الإجمالي لطلبات فحص حول الحساب لهذه المهمة',min_value=1,max_value=200000,value=int(current_policy['max_requests']),step=50,key=f'budget_{selected}')
                    unit_price=st.number_input('سعر الطلب بالدولار (تقدير يدوي من فاتورة RapidAPI، 0 إذا مجهول)',min_value=0.,max_value=100.,value=float(current_policy['unit_price_usd']),step=0.001,format='%.3f',key=f'price_{selected}')
                    usd_cap=st.number_input('حد التكلفة التقديرية بالدولار (0 لتعطيله)',min_value=0.,max_value=1000000.,value=float(current_policy['max_estimated_usd']),step=1.,key=f'usd_cap_{selected}')
                    st.caption('سقف الطلبات حد قطعي داخل مِرصاد. تقدير الدولار لا يساوي فاتورة RapidAPI الفعلية، ولا يشمل سحب المتابعين أو الاستعلام عن الملفات الشخصية.')
                    if st.button('💾 اعتماد ميزانية الإثراء',key=f'budget_save_{selected}'):
                        save_about_policy(db,selected,enabled,cap,unit_price,usd_cap)
                        st.success('تم حفظ إعدادات الإثراء والميزانية لهذه المهمة.');st.rerun()
                progress_info=about_stats(db,selected)
                m1,m2,m3,m4=st.columns(4)
                m1.metric('اكتمل فحص About',f"{progress_info['done']:,} / {progress_info['total']:,}")
                m2.metric('دول متاحة',f"{progress_info['with_country']:,}")
                m3.metric('محاولات محتسبة',f"{progress_info['used_requests']:,} / {progress_info['max_requests']:,}")
                m4.metric('تكلفة تقديرية',f"${progress_info['estimated_usd']:.3f}" if progress_info['unit_price_usd'] else 'السعر غير محدد')
                st.progress(min(1.,progress_info['done']/max(1,progress_info['total'])),text='تقدم إثراء حول الحساب — مستقل عن تقدم سحب المتابعين')
                st.caption(f"أخطاء تنتظر إعادة المحاولة: {progress_info['retry']} | أخطاء نهائية: {progress_info['exhausted']} | يوزرات ناقصة: {progress_info['missing_username']} | طلبات متبقية: {progress_info['remaining_requests']}")
                if st.button('🌍 استكمال إثراء 50 حسابًا / إصلاح السجلات القديمة',key=f'auto_run_{selected}',disabled=not bool(rapid_key and progress_info['enabled'] and progress_info['remaining_requests']>0)):
                    try:
                        reset_interrupted(db,selected)
                        result=run_auto_about(db,selected,rapid_key,rapid_about_path,50)
                        st.success('نتيجة الاستكمال: '+str(result))
                        if result['throttled']:st.warning('تم وقف بقية الطلبات بسبب خطأ أو تقييد من المصدر؛ راجع سجلات المهمة قبل إعادة المحاولة.')
                        st.rerun()
                    except Exception as ex:st.error('تعذر تشغيل الإثراء: '+str(ex))
    
            # Automation runs one bounded cycle per Streamlit fragment tick. It is
            # browser-session driven, not a durable background worker.
            if rapid_key:
                st.markdown('#### ⚡ تشغيل متسلسل تلقائي مع خط زمني')
                st.caption('تُحفظ النتائج بعد كل دورة؛ التشغيل داخل الصفحة يتطلب بقاء الجلسة نشطة.')
                auto_key=f'continuous_{selected}'
                cap_key=f'continuous_page_cap_{selected}'
                if auto_key not in st.session_state:st.session_state[auto_key]=False
                auto_goal=int(target_limit)
                x1,x2,x3=st.columns(3)
                x1.metric('عدد المتابعين المستهدف تلقائيًا',f'{auto_goal:,}' if provider_total is not None else 'حتى نهاية الصفحات')
                page_cap=x2.number_input('سقف إجمالي صفحات السحب (حماية من تكاليف المصدر)',min_value=1,max_value=100000,value=250,step=50,key=cap_key)
                x3.metric('صفحات السحب المستخدمة',f"{job['pages']:,} / {int(page_cap):,}")
                st.caption('حد طلبات About والتكلفة المحددان في «الميزانية وضبط الإثراء» يظلان مفعلين. سقف الصفحات لا يشمل استعلامات جلب بيانات الملفات الشخصية.')
                bstart,bstop=st.columns(2)
                if bstart.button('▶ بدء السحب والإثراء',key=f'continuous_start_{selected}',disabled=not configured or job['pages']>=page_cap):
                    st.session_state[auto_key]=True
                    st.session_state[f'stall_{selected}']=0
                    reset_interrupted(db,selected)
                    add_event(db,selected,'بدء التشغيل الآلي',f'الهدف {auto_goal} | سقف الصفحات {page_cap} | إثراء 50 لكل دورة')
                    st.rerun()
                if bstop.button('⏸ إيقاف وحفظ التقدم',key=f'continuous_stop_{selected}'):
                    st.session_state[auto_key]=False
                    add_event(db,selected,'إيقاف يدوي','حُفظ تقدم المهمة ويمكن استكمالها لاحقًا')
                    st.rerun()
    
                @st.fragment(run_every='4s')
                def continuous_panel(job_id,goal,max_pages,active_key):
                    with closing(connect(db_path)) as _fragment_db:
                        return _continuous_panel_inner(_fragment_db,job_id,goal,max_pages,active_key)
    
                def _continuous_panel_inner(db,job_id,goal,max_pages,active_key):
                    info=auto_snapshot(db,job_id,goal)
                    a=info['about']
                    z1,z2,z3,z4=st.columns(4)
                    z1.metric('المتابعون الفريدون',f"{info['unique']:,} / {goal:,}" if provider_total is not None else f"{info['unique']:,}")
                    z2.metric('فُحصت معلومات About',f"{a['done']:,} / {info['unique']:,}")
                    z3.metric('زمن المهمة منذ إنشائها',f"{info['elapsed_wall_seconds']/60:.1f} دقيقة")
                    z4.metric('الوقت المتبقي (تقديري)',f"{info['eta_seconds']/60:.1f} دقيقة" if info['eta_seconds'] is not None else 'غير متاح')
                    st.progress(min(1.,info['unique']/goal),text='تقدم سحب المتابعين')
                    st.progress(min(1.,a['done']/max(1,info['unique'])),text='تقدم إثراء About')
                    ev=events_as_dicts(db,job_id)
                    if ev:
                        frame=pd.DataFrame(ev)
                        # Separate timeline for observed progress, rather than inventing measurements.
                        if 'at' in frame and 'unique_count' in frame:
                            frame['at']=pd.to_datetime(frame['at'],utc=True,errors='coerce')
                            series=frame.dropna(subset=['at']).groupby('at')['unique_count'].max()
                            if not series.empty:st.line_chart(series.rename('متابعون فريدون'),height=180)
                        with st.expander('🕒 الخط الزمني وسجل الدفعات'):
                            st.dataframe(frame.tail(40).iloc[::-1],hide_index=True,use_container_width=True)
                    if not st.session_state.get(active_key):
                        st.caption('التشغيل التلقائي متوقف. التقدم محفوظ في قاعدة البيانات الحالية.')
                        return
                    row=db.execute('SELECT status,pages FROM jobs WHERE id=?',(job_id,)).fetchone()
                    if row['pages']>=max_pages and info['unique']<goal:
                        st.session_state[active_key]=False
                        st.warning('توقف التشغيل: تم بلوغ سقف صفحات السحب الذي حددته.')
                        add_event(db,job_id,'حد الصفحات','توقف لتجنب تجاوز سقف الطلبات')
                        return
                    with st.spinner('جاري معالجة دفعة تلقائيًا (البيانات تُحفظ بعد كل طلب)...'):
                        result=auto_step(db,job_id,rapid_key=rapid_key,followers_path=rapid_path,
                                         about_path=rapid_about_path,goal=goal,
                                         page_limit=min(int(page_limit),max_pages-int(row['pages'])),
                                         page_count=int(page_count),about_batch=50,
                                         do_fetch=True,do_about=True)
                    st.caption(f"الدورة الحالية: {result['fetched_pages']} صفحة متابعة | {result['about_attempted']} فحص About | {result['about_completed']} اكتمل | متابعون جدد: {result.get('new_unique',0)}")
                    # لا نعيد تدوير صفحات لا تقدم معرفات جديدة إلى ما لا نهاية.
                    stall_key=f'stall_{job_id}'
                    if result['fetched_pages']>0 and result.get('new_unique',0)==0:
                        st.session_state[stall_key]=st.session_state.get(stall_key,0)+1
                    elif result.get('new_unique',0)>0:
                        st.session_state[stall_key]=0
                    if result['state']=='running' and st.session_state.get(stall_key,0)>=2:
                        result['state']='paused'
                        result['message']='توقّف احترازي: دورتان متتاليتان دون أي حساب جديد. افحص Cursor وحدود المزود قبل الاستئناف.'
                        add_event(db,job_id,'حماية من الدوران','دورتان من الصفحات دون حسابات فريدة جديدة')
                    if result['state']!='running':
                        st.session_state[active_key]=False
                        if inspect_job(db,job_id)['approved']:
                            st.success('✅ تم الانتهاء من المهمة')
                        else:
                            st.warning('توقفت المهمة قبل اكتمالها: '+result['message'])
                        if result['snapshot']['job_status']=='مكتمل' and result['snapshot']['unique'] < goal:
                            st.warning('أعاد مزود البيانات نهاية الصفحات عند عدد أقل من الهدف. لن يعيد مِرصاد طلب الصفحات نفسها تلقائيًا؛ راجع حدود مزود البيانات وصلاحيات الحساب.')
                    else:
                        st.success('حُفظ تقدم هذه الدورة؛ ستبدأ الدورة التالية تلقائيًا ما دامت الصفحة متصلة.')
                continuous_panel(selected,int(auto_goal),int(page_cap),auto_key)
    
            disabled=not configured or job['status']=='مكتمل' or bool(st.session_state.get(f'continuous_{selected}',False))
            if st.button('▶ تنفيذ دفعة جديدة / متابعة',disabled=disabled,type='primary'):
                progress=st.progress(0)
                status=st.empty()
                def notify(page,inserted,duplicates):
                    progress.progress(min(1.,page/page_limit))
                    status.write(f'الصفحة {page} من {page_limit} | جديد {inserted} | تكرار {duplicates}')
                try:
                    count=(fetch_rapid(db,selected,rapid_key,rapid_path,page_limit,int(target_limit),int(page_count),notify) if rapid_key else fetch_official(db,selected,token,page_limit,int(target_limit),notify))
                    if rapid_key and about_policy(db,selected)['enabled']:
                        with st.spinner('إثراء تلقائي: فحص حتى 50 حسابًا وفق ميزانية المهمة...'):
                            about_result=run_auto_about(db,selected,rapid_key,rapid_about_path,50)
                        st.info(f"إثراء About: {about_result['completed']} مكتمل من {about_result['attempted']} طلب، والأخطاء المؤقتة: {about_result['temporary_errors']}")
                        if about_result['throttled']:st.warning('أوقفت بقية الاستعلامات حمايةً من الأخطاء المتكررة أو حدود المزود.')
                    st.success(f'اكتملت {count} صفحة. البيانات محفوظة ويمكن عرضها في تبويب التقارير.')
                    st.rerun()
                except (XAPIError,RapidAPIError) as e:st.error(str(e))
            if job['status']=='مكتمل':
                st.success('✅ تم الانتهاء من المهمة', icon=None)
        else:st.info('أنشئ مهمة جديدة أو استورد CSV لتجربة التقارير دون X API.')

if page == '📥 استيراد البيانات':
    st.subheader('استيراد بيانات منصات التواصل')
    platform=st.selectbox('المنصة',options=['X','TikTok','Instagram'])
    label=st.text_input('عنوان المجموعة',placeholder='مثال: عينة متابعين 2026')
    file=st.file_uploader('ملف CSV بصيغة UTF-8',type=['csv'])
    st.caption('الحقول الأساسية: id أو user_id أو uid أو «المعرف الدائم»، ويمكن إضافة username وname. لا يُطلب مفتاح API للاستيراد.')
    if st.button('استيراد وحفظ',disabled=not bool(file and label.strip()),type='primary'):
        try:
            # Prevent uncontrolled memory use in public deployments.
            blob=file.getvalue()
            if len(blob)>30*1024*1024:raise ValueError('حجم الملف تجاوز 30 ميجابايت. قسمه إلى عدة ملفات أصغر.')
            job_id=create_job(db,platform,label.strip()[:100])
            inserted=import_csv(db,job_id,blob)
            add_event(db,job_id,'استيراد',f'حسابات جديدة: {inserted}')
            st.success(f'تم استيراد {inserted:,} سجل فريد إلى المهمة #{job_id}.')
        except Exception as e:st.error(f'تعذّر الاستيراد: {e}')
    st.download_button('تحميل قالب CSV تجريبي',data='id,username,name,protected,verified\n1234567890123456789,example,Example User,false,false\n',file_name='mirsad_template.csv',mime='text/csv')

if page == '📊 التحليلات والتقارير':
    st.subheader('التحليلات والتصدير')
    jobs=job_rows(db)
    if not jobs:st.info('لا توجد مهام بعد. ابدأ سحبًا أو استورد CSV.');
    else:
        opts={r['id']:f"#{r['id']} | {r['platform']} | {r['target']} | {r['status']}" for r in jobs}
        selected=st.selectbox('اختر التقرير',list(opts),format_func=lambda k:opts[k],key='report_job')
        job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(selected,)).fetchone())
        if job['platform']=='X' and rapid_key:
            rep=about_stats(db,selected)
            st.caption(f"إثراء About: {rep['done']:,}/{rep['total']:,} | طلبات مستخدمة {rep['used_requests']:,} | دول مسجلة {rep['with_country']:,} | ينتظر إعادة المحاولة {rep['retry']:,}")
        rows=followers_as_dicts(db,selected)
        events=events_as_dicts(db,selected)
        eligible_rows = exportable_rows(rows)
        countries=Counter(r['account_country'] for r in eligible_rows)
        a,b,c,d=st.columns(4)
        a.metric('فريد',f'{len(rows):,}')
        b.metric('الدول المصنفة',len(countries))
        c.metric('غير محددة',f"{len(rows)-len(eligible_rows):,}")
        d.metric('صفحات / دفعات',f"{job['pages']:,}")
        if rows:
            with st.expander('🧹 إصلاح القيم الفاسدة المسجلة سابقًا'):
                st.caption('يحوّل القيم غير المنطقية مثل الاسم 16 وموقع مكتوب كقاموس إلى حقول فارغة ثم يمكن إعادة محاولة استكمالها من API.')
                if st.button('تنظيف السجلات القديمة في هذه المهمة',key=f'clean_{selected}'):
                    repaired=repair_legacy_rows(db,selected)
                    st.success(f'تم تنظيف {repaired} سجل. لم يتم تخمين بيانات غير متاحة.')
                    st.rerun()
            missing=sum(1 for r in rows if not r.get('name') or not r.get('username'))
            if missing:
                st.warning(f'يوجد {missing:,} حساب ناقص الاسم أو اليوزر؛ هذه قيم مفقودة في الاستجابة المخزنة وليست مشكلة في Excel.')
                if rapid_key and job['platform']=='X':
                    st.markdown('#### 🔧 استكمال الحسابات الناقصة تلقائيًا عبر RapidAPI')
                    st.caption('يستخدم مزود twitter241 وظيفة Get Users By IDs (المسار /get-users، الحقل users). كل دفعة استعلام إضافي قد تُحتسب من باقتك. لا يبدأ أي طلب إلا بعد الضغط على الزر.')
                    c_batch,c_number=st.columns(2)
                    profile_batch=c_batch.number_input('معرّفات في طلب المعلومات',min_value=1,max_value=100,value=20,step=1,key=f'p_batch_{selected}')
                    profile_pages=c_number.number_input('أقصى عدد طلبات استكمال لهذه الضغطة',min_value=1,max_value=100,value=5,step=1,key=f'p_pages_{selected}')
                    if st.button('🔍 فحص استجابة تفاصيل 3 حسابات (طلب واحد مدفوع محتمل)',key=f'diagnose_{selected}'):
                        try:
                            sample_ids=[str(r['uid']) for r in rows[:3]]
                            with st.spinner('فحص بنية استجابة المزود...'):
                                stat=diagnose_lookup(sample_ids,rapid_key,rapid_profiles_path)
                            st.json(stat)
                            if not stat['matched']:
                                st.warning('لم يعِد المسار ملفات مطابقة للمعرّفات. لا تكرر الطلبات قبل مراجعة مسار Get Users By IDs.')
                        except RapidAPIError as e:st.error(str(e))
                    if st.button('🔄 جلب الأسماء واليوزرات الناقصة من RapidAPI',key=f'profiles_{selected}',type='primary'):
                        try:
                            with st.spinner('يجري استعلام ملفات الحسابات الموجودة...'):
                                queried,matched,changed=enrich_missing_job(db,selected,rapid_key,rapid_profiles_path,int(profile_batch),int(profile_pages))
                            if queried and not matched:
                                st.warning(f'تمت مطالبة المزود بتفاصيل {queried} حساب دون استرجاع ملفات مطابقة. تحقق من استجابة GET /get-users، ولا تكرر الطلبات المدفوعة حتى تفحص النتيجة.')
                            else:
                                st.success(f'استعلامات لعدد {queried} معرف | تفاصيل مطابقة {matched} | سجلات تحسنت {changed}')
                            st.rerun()
                        except RapidAPIError as e:st.error(str(e))
                with st.expander('🛠️ استكمال البيانات الناقصة من ملف استجابة JSON'):
                    st.caption('يمكن استيراد استجابة Get User Followers أو Get Users By IDs التي تتضمن أسماء الحسابات. لن تُضاف حسابات جديدة؛ سيُستكمل فقط ما يطابق المعرف الدائم.')
                    enrichment=st.file_uploader('اختر JSON يحتوي على بيانات الحسابات',type=['json','txt'],key=f'enrich_{selected}')
                    if enrichment and st.button('استكمال الاسم واليوزر والموقع',key=f'enrich_btn_{selected}'):
                        try:
                            if enrichment.size>20*1024*1024:raise ValueError('الحد الأقصى للملف 20 ميجابايت')
                            payload=json.loads(enrichment.getvalue().decode('utf-8-sig'))
                            if isinstance(payload,list):
                                users=[u for entry in payload if (u:=__import__('rapid_client').parse_user(entry))]
                            else:
                                users,_=extract_users(payload)
                            matching,changed=enrich_existing(db,selected,users)
                            st.success(f'الحسابات المطابقة {matching} | السجلات المحسنة {changed}.')
                            st.rerun()
                        except (ValueError,RapidAPIError) as exc:st.error(str(exc))
            if rapid_key and job['platform']=='X':
                with st.expander('🌍 فحص «حول الحساب»: بلد الحساب / متصل عبر / حالة الإيقاف', expanded=True):
                    cs=connection_stats(db,selected)
                    st.caption(f"متصل عبر: متاح لـ {cs['known']:,} من {cs['checked']:,} حسابًا تم فحصه")
                    if cs['unavailable']:
                        retry_n=st.number_input('إعادة فحص سجلات الاتصال الناقصة (طلبات مدفوعة محتملة)',min_value=1,max_value=200,value=min(20,cs['unavailable']),key=f'conn_retry_n_{selected}')
                        if st.button('إعادة جدولة فحص متصل عبر',key=f'conn_retry_{selected}'):
                            n=requeue_missing_connection(db,selected,int(retry_n))
                            st.info(f'أُعيدت جدولة {n} حسابًا؛ شغّل إثراء About لفحصها مجددًا. قد تُحتسب طلبات جديدة.')

                    st.caption('المسار twitter241: GET /about-account والوسيط username. يتم فحص كل حساب بطلب مستقل قد يكون مدفوعًا. الحالة غير مؤكدة إذا لم يصرح المصدر بها. لا نستنتج بلد الحساب من الموقع المعلن.')
                    valid=[r for r in rows if r.get('username')]
                    if not valid:
                        st.warning('لا توجد يوزرات في هذا التقرير. استكمل الأسماء واليوزرات أولًا عبر Get Users By IDs.')
                    else:
                        sample=st.selectbox('اختر حسابًا لاختبار الاستجابة', options=valid[:100],format_func=lambda r:f"@{r['username']} ({r['uid']})",key=f'about_id_{selected}')
                        if st.button('🔍 اختبار حول حساب واحد (طلب واحد محتمل التكلفة)',key=f'about_test_{selected}'):
                            try:
                                detail=lookup_about_diagnostic(sample['username'],rapid_key,rapid_about_path)
                                st.json(detail)
                                st.caption('معلومات التشخيص تتضمن أسماء الحقول وأنواعها فقط، دون رموز API أو قيم الحساب. إذا لم تُستخرج بيانات، لا تُشغل فحصًا جماعيًا.')
                                if not detail['any_about_value']:
                                    st.warning('المزود لم يعِد حقولًا صريحة للدولة/متصل عبر/الإيقاف. راجع مفاتيح الاستجابة المعروضة، ولا تشغّل فحصًا جماعيًا بعد.')
                                else:
                                    st.success('عاد حقل صريح واحد على الأقل. يمكنك تشغيل فحص دفعة محدودة.')
                            except RapidAPIError as e:st.error(str(e))
                        per_click=st.number_input('أقصى حسابات تفحص في هذه الضغطة (قد تكون طلبات مدفوعة)',min_value=1,max_value=100,value=3,step=1,key=f'about_limit_{selected}')
                        consent=st.checkbox('أوافق على عدد الطلبات المحتمل وأريد فحص الحسابات التي لم تُفحص سابقًا',key=f'about_consent_{selected}')
                        if st.button('🌍 استكمال بيانات «حول الحساب»',disabled=not (consent and about_stats(db,selected)['enabled'] and about_stats(db,selected)['remaining_requests']>0),key=f'about_run_{selected}',type='primary'):
                            try:
                                with st.spinner('يتم فحص حسابات لم تُفحص سابقًا...'):
                                    stats=run_auto_about(db,selected,rapid_key,rapid_about_path,int(per_click))
                                st.success('نتيجة الفحص: '+str(stats))
                                st.rerun()
                            except RapidAPIError as e:st.error(str(e))
                    st.caption('حالة الإيقاف لا يمكن ضمان اكتشافها لقائمة متابعين موجودة؛ فحص About لا يعادل فحص حالة حساب موقوف. استخدم مصدر حالة مستقلًا إن توفر.')
            st.write('#### الحسابات حسب دولة «حول الحساب» (عندما تتوفر)')
            st.bar_chart(pd.Series(countries).sort_values(ascending=False))
            protected=Counter(r['account_country'] for r in eligible_rows if r.get('protected')==1)
            st.write('#### الحسابات المحمية حسب الدولة')
            st.bar_chart(pd.Series(protected) if protected else pd.Series({'لا توجد حالات محمية مؤكدة':0}))
            preview=pd.DataFrame(rows[:500])
            # Display check status explicitly instead of misleading None values.
            if 'about_checked_at' in preview.columns:
                checked=preview['about_checked_at'].fillna('').astype(str).str.strip().ne('')
                for k in ('account_country','connected_via','suspension_status'):
                    preview[k]=preview[k].where(preview[k].notna() & preview[k].astype(str).str.strip().ne(''), checked.map({True:'غير متاح من المصدر',False:'لم يُفحص بعد'}))
            preview['حالة الحساب'] = preview.apply(lambda r: account_status(r.to_dict()), axis=1)
            preview['متصل عبر'] = preview.apply(lambda r: connected_via(r.to_dict()), axis=1)
            preview['رابط المعرف الدائم'] = preview.apply(lambda r: account_links(r.to_dict())[0], axis=1)
            preview['رابط الحساب'] = preview.apply(lambda r: account_links(r.to_dict())[1], axis=1)
            preview['رابط حول الحساب'] = preview.apply(lambda r: account_links(r.to_dict())[2], axis=1)
            st.dataframe(preview[['name','username','uid','رابط المعرف الدائم','رابط الحساب','رابط حول الحساب','account_country','متصل عبر','حالة الحساب','protected','verified']],hide_index=True,use_container_width=True)
            st.caption('الحالة «فعال / موقوف / محجوز في السعودية» لا تعرض إلا بدليل صريح. الرابط /about يُنشأ بصيغة صحيحة ولا يضمن إمكانية فتح صفحة الحساب المقيد.')
            st.caption('يظهر أول 500 سجل للمعاينة فقط، بينما Excel يتضمن جميع السجلات المحفوظة.')
            readiness=inspect_job(db,selected)
            st.markdown('#### التحقق من اكتمال المهمة')
            ra,rb,rc=st.columns(3)
            ra.metric('نهاية صفحات السحب','مؤكدة' if readiness['fetch_complete'] else 'غير مؤكدة')
            rb.metric('فحوص About الناجحة',f"{readiness['about_done']:,} / {readiness['total']:,}")
            rc.metric('حسابات بدولة من About',f"{readiness['with_country']:,}")
            if readiness['approved']:
                st.success('✅ تم الانتهاء من المهمة — التقرير النهائي مؤهل للاعتماد')
            else:
                st.warning('تقرير أولي غير معتمد: '+readiness['reason'])
            st.markdown('#### 📦 تنزيل التقارير المهنية')
            eligible_rows = exportable_rows(rows)
            excluded_count = len(rows) - len(eligible_rows)
            st.caption(f'سيُدرج في التقرير النهائي {len(eligible_rows):,} حسابًا محدد الدولة، مع استبعاد {excluded_count:,} حسابًا بلا دولة متاحة. تبقى السجلات المستبعدة محفوظة داخل النظام.')
            st.caption('Excel للبيانات التفصيلية، Word للتقرير الإداري القابل للتحرير، وPowerPoint للعرض التنفيذي. جميع الملفات تعتمد السجلات المحفوظة وقت التصدير.')
            display_name=st.text_input('اسم الحساب المستهدف ليظهر في اسم الملف (اختياري)',value=job['target'],key=f'report_display_name_{selected}')
            output_username=job['target']
            timing=elapsed_metrics(job, events)
            t1,t2,t3=st.columns(3)
            t1.metric('زمن طلبات سحب المتابعين',fmt_duration(timing['api_seconds']))
            t2.metric('زمن المهمة حتى آخر تحديث',fmt_duration(timing['wall_seconds']))
            t3.metric('متوسط زمن الصفحة',fmt_duration(timing['seconds_per_page']))
            st.caption('زمن طلبات السحب لا يشمل فحوص About. لا تتوفر مدة مستقلة دقيقة للإثراء في السجلات القديمة؛ تعكس مدة المهمة الفواصل والتوقف أيضًا.')
            ex_col, word_col, ppt_col = st.columns(3)
            with ex_col:
                st.download_button(('⬇️ تقرير Excel النهائي' if readiness['approved'] else '⬇️ تقرير Excel أولي'),data=make_excel(eligible_rows,events,job),file_name=safe_report_name(display_name,output_username,'xlsx'),mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',type='primary',use_container_width=True,key=f'xlsx_{selected}')
            with word_col:
                st.download_button(('⬇️ تقرير Word النهائي' if readiness['approved'] else '⬇️ تقرير Word أولي'),data=make_word(eligible_rows,events,job),file_name=safe_report_name(display_name,output_username,'docx'),mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',use_container_width=True,key=f'docx_{selected}')
            with ppt_col:
                st.download_button(('⬇️ عرض PowerPoint النهائي' if readiness['approved'] else '⬇️ عرض PowerPoint أولي'),data=make_powerpoint(eligible_rows,events,job),file_name=safe_report_name(display_name,output_username,'pptx'),mime='application/vnd.openxmlformats-officedocument.presentationml.presentation',use_container_width=True,key=f'pptx_{selected}')
        if events:
            st.write('#### سجل الوقت والتقدم')
            st.dataframe(pd.DataFrame(events),hide_index=True,use_container_width=True)
        st.info('تعتمد الإحصاءات والتقارير حصريًا على دولة الحساب من صفحة «حول الحساب»؛ تستبعد الحسابات بلا دولة متاحة من التصدير، ولا تُحذف من قاعدة البيانات. لا تُستنتج حالة الإيقاف من غياب النتائج.')

if page == '⚙️ دليل المنصات':
    st.subheader('حالة الاتصال بحسب المنصة')
    st.markdown('**X:** يدعم RapidAPI (مع المسار الصحيح) أو واجهة X الرسمية. في وضع RapidAPI يحتاج معرف الحساب الرقمي. **إنستغرام:** استيراد CSV وتحليل وتصدير؛ لا يوجد محرك مستقل مصرح به لسحب جميع المتابعين. **تيك توك:** استيراد CSV وتحليل وتصدير؛ لا يوجد محرك مضمون لسحب جميع المتابعين.')
    st.markdown('**الصفحة About:** رابطها متاح داخل Excel للفحص اليدوي في X. لا نفترض أن حقول الدولة أو «متصل عبر» متاحة عبر X API الرسمي.')
    st.markdown('**الإنتاج:** ملفات SQLite المحلية على الاستضافات المؤقتة قد تُفقد عند إعادة تشغيل الخادم. لمهام كبيرة ومتكررة استخدم قرصًا دائمًا أو PostgreSQL مع عامل مهام مستقل قبل الاعتماد التشغيلي.')
    st.caption('لا ترسل أي مفاتيح API داخل المحادثات أو الصور. احفظها فقط في أسرار الاستضافة.')
