"""مِرصاد | Independent Arabic multi-platform monitoring dashboard."""
import hmac
import json
import os
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

from core import (connect,create_job,add_event,followers_as_dicts,events_as_dicts,
                  import_csv,job_rows,username_from_input,utcnow,enrich_existing,repair_legacy_rows)
from exporter import make_excel
from report_export import make_word, make_powerpoint
from filtering import exportable_rows
from x_client import XAPIError,lookup_user,fetch_batch as fetch_official
from rapid_client import RapidAPIError, fetch_batch as fetch_rapid, validate_path, extract_users, enrich_missing_job, diagnose_lookup, lookup_about, lookup_about_diagnostic, enrich_about_job
from auto_enrich import policy as about_policy, set_policy as save_about_policy, stats as about_stats, run_batch as run_auto_about, reset_interrupted
from auto_runner import step as auto_step, snapshot as auto_snapshot
from report_fields import safe_report_name, account_links, account_status, connected_via, elapsed_metrics, fmt_duration

st.set_page_config(page_title='مِرصاد | ذكاء المنصات الاجتماعية',page_icon='🛰️',layout='wide',initial_sidebar_state='expanded')
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;600;700;800&display=swap');
html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {direction:rtl; text-align:right; font-family:'Tajawal',Tahoma,Arial,sans-serif;}
/* Streamlit Sidebar and Main are laid out from right to left */
[data-testid="stAppViewContainer"] {flex-direction:row-reverse !important;}
section[data-testid="stSidebar"] {border-right:0 !important;border-left:1px solid #dbe4eb !important;background:#f6f9fc !important;}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap:.55rem;}
section[data-testid="stSidebar"] label {font-family:inherit;}
section[data-testid="stSidebar"] [role="radiogroup"] {display:flex;flex-direction:column;gap:7px;}
section[data-testid="stSidebar"] [role="radiogroup"] label {display:flex;align-items:center;justify-content:flex-start;border:1px solid #e2e9ef;border-radius:11px;background:#ffffff;padding:12px 10px;margin:0;min-height:43px;transition:background .2s,border .2s;}
section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#e2f3f2;border-color:#0f9188;color:#0b564f;font-weight:800;}
section[data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#edf7f7;}
[data-testid="stMainBlockContainer"] {max-width:1400px;padding:1.5rem 2rem 3rem;}
[data-testid="stTextInput"] input, [data-testid="stNumberInput"] input {direction:ltr;text-align:left;}
[data-testid="stMetricValue"] {direction:ltr;text-align:right;font-family:'Tajawal',sans-serif;}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {direction:ltr;}
h1,h2,h3,h4,p,button,span {font-family:'Tajawal',Tahoma,Arial,sans-serif;}
.hero{padding:28px 32px;background:linear-gradient(115deg,#0c2836,#16505b);color:white;border-radius:18px;margin-bottom:20px;box-shadow:0 8px 24px #15384b14;}
.hero h1{font-size:2.3rem;margin:0;color:white;font-weight:800;}.hero p{margin:8px 0 0;color:#d1ecea;font-size:1rem;}
.small{color:#6b7280;font-size:13px}
.section-title{border-right:4px solid #159a8c;padding-right:12px;margin:14px 0 18px;font-weight:800;color:#143546}
section[data-testid="stSidebar"] h1 {font-size:1.35rem;}
div[data-testid="stMetric"] {background:#fff;border:1px solid #e3eaf0;border-radius:13px;padding:13px 17px;}
div[data-testid="stAlert"] {border-radius:11px;}
@media(max-width:800px){[data-testid="stMainBlockContainer"]{padding:1rem}.hero{padding:17px}.hero h1{font-size:1.55rem}}
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

st.markdown('<div class="hero"><h1>🛰️ مِرصاد</h1><p>ذكاء المنصات الاجتماعية | رصد، تحليل، تقارير</p></div>',unsafe_allow_html=True)
st.sidebar.title('🛰️ مِرصاد')
admin=signed_in()
if not admin:
    st.subheader('المرصد الرقمي متعدد المنصات')
    c1,c2,c3=st.columns(3)
    c1.metric('منصة X','متاحة عند ربط API')
    c2.metric('تيك توك','استيراد ملفات')
    c3.metric('إنستغرام','استيراد ملفات')
    st.info('الموقع عام، لكن أدوات استخراج البيانات والملفات محمية بالدخول. لا تُعرض أي مفاتيح API للزوار.')
    st.markdown('**المزايا:** معرّفات دائمة بصيغة نص، تنظيف التكرارات، تحليل الموقع المعلن، مخططات وتصدير Excel عربي بخمس أوراق.')
    st.caption('لا يمكن اعتبار بلد المستخدم من صفحة «حول الحساب» متاحًا ضمن X API الرسمي للمتابعين. البيانات الجغرافية المستخرجة من الموقع المعلن تقديرية.')
    if not secret('ADMIN_PASSWORD'):st.warning('لم تُضبط ADMIN_PASSWORD في إعدادات أسرار الخادم، ولذلك لوحة الإدارة معطلة حمايةً للبيانات.')
    st.stop()

with st.sidebar:
    st.success('لوحة المسؤول مفتوحة')
    if st.button('تسجيل الخروج',use_container_width=True):
        st.session_state['authenticated']=False;st.rerun()
    st.caption('المفاتيح محفوظة في إعدادات الخادم ولا تظهر هنا.')

try:db=connect(secret('MIRSAD_DB') or 'mirsad.sqlite3')
except Exception as e:
    st.error('تعذّر فتح قاعدة البيانات. تحقق من إعدادات التخزين.');st.stop()
token=secret('X_BEARER_TOKEN')
rapid_key=secret('RAPIDAPI_KEY')
rapid_path=secret('RAPIDAPI_FOLLOWERS_PATH')
rapid_profiles_path=secret('RAPIDAPI_PROFILES_PATH') or '/get-users'
rapid_about_path=secret('RAPIDAPI_ABOUT_PATH') or '/about-account'
api_mode='RapidAPI' if rapid_key else 'X الرسمية'
configured=bool(rapid_key and rapid_path) if rapid_key else bool(token)

# قائمة رئيسية عمودية على يمين الموقع — كل قسم مستقل
with st.sidebar:
    st.markdown('### 🧭 القائمة الرئيسية')
    page = st.radio('الأقسام', ['🏠 الرئيسية', '🔎 سحب متابعي X', '📥 استيراد البيانات', '📊 التحليلات والتقارير', '⚙️ دليل المنصات'],
        label_visibility='collapsed', key='mirsad_section')
    st.caption('مِرصاد — الإصدار العربي | API والبيانات محفوظة على الخادم')

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
    st.subheader('سحب متابعي حساب عام على X')
    st.caption('مصدر السحب الحالي: '+api_mode+' | تُحسب الطلبات حسب باقة المزود. يتطلب RapidAPI معرفًا رقميًا دائمًا للحساب.')
    user_raw=st.text_input('اليوزر أو رابط حساب X',placeholder='https://x.com/example')
    c1,c2=st.columns(2)
    target_limit=c1.number_input('الحد المستهدف للحسابات الفريدة',min_value=100,max_value=1000000,value=50000,step=1000)
    page_limit=c2.slider('أقصى صفحات لكل ضغطة',1,20,3)
    st.caption('يمكن استكمال المهمة باستخدام مؤشر الصفحة المحفوظ. التقدم محسوب نحو الحد المستهدف، ولا يضمن ظهور جميع متابعي الحساب.')
    if rapid_key and not rapid_path:st.warning('مفتاح RapidAPI محفوظ، لكن يجب إضافة RAPIDAPI_FOLLOWERS_PATH من توثيق المسار الفعلي إلى Secrets.')
    elif not configured:st.warning('لا يوجد اتصال مكتمل. ضبط مفتاح RapidAPI ومساره، أو X_BEARER_TOKEN، لتفعيل السحب.')
    if rapid_key:
        target_uid_input=st.text_input('المعرف الرقمي الدائم للحساب المستهدف (User ID)',placeholder='2455740283')
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
        a,b,c,d=st.columns(4)
        a.metric('حسابات فريدة',f'{unique:,}')
        b.metric('سجلات تمت معالجتها',f"{job['scanned']:,}")
        c.metric('تكرارات مستبعدة',f"{job['duplicates']:,}")
        d.metric('وقت API المسجل',f"{job['elapsed_seconds']:.1f} ث")
        st.progress(min(1.,unique/target_limit),text=f'التقدم نحو حد {target_limit:,} حساب')
        if job['error']:st.warning('آخر خطأ: '+job['error'])
        if rapid_key:
            st.markdown('#### 🌍 إثراء «حول الحساب» التلقائي — مستقل عن السحب')
            current_policy=about_policy(db,selected)
            with st.expander('الميزانية وضبط الإثراء',expanded=True):
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
            st.caption('اضغط تشغيل مرة واحدة؛ ستتتابع صفحات المتابعين ودفعات إثراء About (حتى 50 حسابًا لكل دورة) ما دام المتصفح متصلًا والتطبيق نشطًا. لا يعمل كخدمة خلفية عند إغلاق الصفحة.')
            auto_key=f'continuous_{selected}'
            goal_key=f'continuous_goal_{selected}'
            cap_key=f'continuous_page_cap_{selected}'
            if auto_key not in st.session_state:st.session_state[auto_key]=False
            x1,x2,x3=st.columns(3)
            auto_goal=x1.number_input('الهدف النهائي للحسابات الفريدة',min_value=1,max_value=1000000,value=int(target_limit),step=100,key=goal_key)
            page_cap=x2.number_input('سقف إجمالي صفحات السحب (حماية من تكاليف المصدر)',min_value=1,max_value=100000,value=250,step=50,key=cap_key)
            x3.metric('صفحات السحب المستخدمة',f"{job['pages']:,} / {int(page_cap):,}")
            st.caption('حد طلبات About والتكلفة المحددان في «الميزانية وضبط الإثراء» يظلان مفعلين. سقف الصفحات لا يشمل استعلامات جلب بيانات الملفات الشخصية.')
            bstart,bstop=st.columns(2)
            if bstart.button('▶ بدء التشغيل المتسلسل',key=f'continuous_start_{selected}',disabled=not configured or job['pages']>=page_cap):
                st.session_state[auto_key]=True
                st.session_state[f'stall_{selected}']=0
                reset_interrupted(db,selected)
                add_event(db,selected,'بدء التشغيل الآلي',f'الهدف {auto_goal} | سقف الصفحات {page_cap} | إثراء 50 لكل دورة')
                st.rerun()
            if bstop.button('⏸ إيقاف التشغيل التلقائي',key=f'continuous_stop_{selected}'):
                st.session_state[auto_key]=False
                add_event(db,selected,'إيقاف يدوي','حُفظ تقدم المهمة ويمكن استكمالها لاحقًا')
                st.rerun()

            @st.fragment(run_every='4s')
            def continuous_panel(job_id,goal,max_pages,active_key):
                info=auto_snapshot(db,job_id,goal)
                a=info['about']
                z1,z2,z3,z4=st.columns(4)
                z1.metric('المتابعون الفريدون',f"{info['unique']:,} / {goal:,}")
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
                    st.info('انتهت أو توقفت الدورة الآلية: '+result['message'])
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
        if job['status']=='مكتمل':st.success('أعيد مؤشر الصفحة فارغًا من X، وانتهت صفحات هذه المهمة.')
    else:st.info('أنشئ مهمة جديدة أو استورد CSV لتجربة التقارير دون X API.')

if page == '📥 استيراد البيانات':
    st.subheader('استيراد بيانات منصات التواصل')
    platform=st.selectbox('المنصة',options=['X','TikTok','Instagram'])
    label=st.text_input('عنوان المجموعة',placeholder='مثال: عينة متابعين 2026')
    file=st.file_uploader('ملف CSV بصيغة UTF-8',type=['csv'])
    st.caption('الحقول الأساسية: id أو user_id أو uid أو «المعرف الدائم»، ويمكن إضافة username وname وlocation. لا يُطلب مفتاح API للاستيراد.')
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
    st.download_button('تحميل قالب CSV تجريبي',data='id,username,name,location,protected,verified\n1234567890123456789,example,Example User,Riyadh,false,false\n',file_name='mirsad_template.csv',mime='text/csv')

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
        countries=Counter(r.get('account_country') or 'غير محددة' for r in rows)
        a,b,c,d=st.columns(4)
        a.metric('فريد',f'{len(rows):,}')
        b.metric('الدول المصنفة',sum(1 for key in countries if key!='غير محددة'))
        c.metric('غير محددة',f"{countries.get('غير محددة',0):,}")
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
            protected=Counter(r.get('account_country') or 'غير محددة' for r in rows if r.get('protected')==1)
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
            st.dataframe(preview[['name','username','uid','رابط المعرف الدائم','رابط الحساب','رابط حول الحساب','country','location','account_country','متصل عبر','حالة الحساب','protected','verified']],hide_index=True,use_container_width=True)
            st.caption('الحالة «فعال / موقوف / محجوز في السعودية» لا تعرض إلا بدليل صريح. الرابط /about يُنشأ بصيغة صحيحة ولا يضمن إمكانية فتح صفحة الحساب المقيد.')
            st.caption('يظهر أول 500 سجل للمعاينة فقط، بينما Excel يتضمن جميع السجلات المحفوظة.')
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
                st.download_button('⬇️ تقرير Excel',data=make_excel(eligible_rows,events,job),file_name=safe_report_name(display_name,output_username,'xlsx'),mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',type='primary',use_container_width=True,key=f'xlsx_{selected}')
            with word_col:
                st.download_button('⬇️ تقرير Word',data=make_word(eligible_rows,events,job),file_name=safe_report_name(display_name,output_username,'docx'),mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',use_container_width=True,key=f'docx_{selected}')
            with ppt_col:
                st.download_button('⬇️ عرض PowerPoint',data=make_powerpoint(eligible_rows,events,job),file_name=safe_report_name(display_name,output_username,'pptx'),mime='application/vnd.openxmlformats-officedocument.presentationml.presentation',use_container_width=True,key=f'pptx_{selected}')
        if events:
            st.write('#### سجل الوقت والتقدم')
            st.dataframe(pd.DataFrame(events),hide_index=True,use_container_width=True)
        st.info('تعتمد الإحصاءات الجغرافية أعلاه على account_country من خدمة «حول الحساب» عند توفره؛ المواقع المعلنة بيانات مختلفة. ولا تُستنتج حالة الإيقاف من غياب النتائج.')

if page == '⚙️ دليل المنصات':
    st.subheader('حالة الاتصال بحسب المنصة')
    st.markdown('**X:** يدعم RapidAPI (مع المسار الصحيح) أو واجهة X الرسمية. في وضع RapidAPI يحتاج معرف الحساب الرقمي. **إنستغرام:** استيراد CSV وتحليل وتصدير؛ لا يوجد محرك مستقل مصرح به لسحب جميع المتابعين. **تيك توك:** استيراد CSV وتحليل وتصدير؛ لا يوجد محرك مضمون لسحب جميع المتابعين.')
    st.markdown('**الصفحة About:** رابطها متاح داخل Excel للفحص اليدوي في X. لا نفترض أن حقول الدولة أو «متصل عبر» متاحة عبر X API الرسمي.')
    st.markdown('**الإنتاج:** ملفات SQLite المحلية على الاستضافات المؤقتة قد تُفقد عند إعادة تشغيل الخادم. لمهام كبيرة ومتكررة استخدم قرصًا دائمًا أو PostgreSQL مع عامل مهام مستقل قبل الاعتماد التشغيلي.')
    st.caption('لا ترسل أي مفاتيح API داخل المحادثات أو الصور. احفظها فقط في أسرار الاستضافة.')
