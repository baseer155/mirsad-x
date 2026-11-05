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
from x_client import XAPIError,lookup_user,fetch_batch as fetch_official
from rapid_client import RapidAPIError, fetch_batch as fetch_rapid, validate_path, extract_users, enrich_missing_job, diagnose_lookup, lookup_about

st.set_page_config(page_title='مِرصاد | ذكاء المنصات الاجتماعية',page_icon='🛰️',layout='wide',initial_sidebar_state='expanded')
st.markdown('''<style>
html, body, [data-testid="stApp"], [data-testid="stSidebar"] {direction:rtl;text-align:right;font-family:Tahoma,Arial,sans-serif}
[data-testid="stTextInput"] input {direction:ltr;text-align:left}
[data-testid="stMetricValue"] {direction:ltr;text-align:right}
section[data-testid="stSidebar"]{border-left:1px solid #ccd5d8}
.hero{padding:22px 24px;background:linear-gradient(100deg,#102b38,#1c5660);color:white;border-radius:15px;margin-bottom:18px}
.hero h1{font-size:2rem;margin:0;color:white}.hero p{margin:8px 0 0;color:#d1ecea}
.small{color:#6b7280;font-size:13px}
</style>''',unsafe_allow_html=True)

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
rapid_about_path=secret('RAPIDAPI_ABOUT_PATH')
api_mode='RapidAPI' if rapid_key else 'X الرسمية'
configured=bool(rapid_key and rapid_path) if rapid_key else bool(token)

s1,s2,s3,s4=st.tabs(['🔎 سحب متابعي X','📥 استيراد البيانات','📊 التقارير وExcel','⚙️ دليل المنصات'])
with s1:
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
        disabled=not configured or job['status']=='مكتمل'
        if st.button('▶ تنفيذ دفعة جديدة / متابعة',disabled=disabled,type='primary'):
            progress=st.progress(0)
            status=st.empty()
            def notify(page,inserted,duplicates):
                progress.progress(min(1.,page/page_limit))
                status.write(f'الصفحة {page} من {page_limit} | جديد {inserted} | تكرار {duplicates}')
            try:
                count=(fetch_rapid(db,selected,rapid_key,rapid_path,page_limit,int(target_limit),int(page_count),notify) if rapid_key else fetch_official(db,selected,token,page_limit,int(target_limit),notify))
                st.success(f'اكتملت {count} صفحة. البيانات محفوظة ويمكن عرضها في تبويب التقارير.')
                st.rerun()
            except (XAPIError,RapidAPIError) as e:st.error(str(e))
        if job['status']=='مكتمل':st.success('أعيد مؤشر الصفحة فارغًا من X، وانتهت صفحات هذه المهمة.')
    else:st.info('أنشئ مهمة جديدة أو استورد CSV لتجربة التقارير دون X API.')

with s2:
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

with s3:
    st.subheader('التحليلات والتصدير')
    jobs=job_rows(db)
    if not jobs:st.info('لا توجد مهام بعد. ابدأ سحبًا أو استورد CSV.');
    else:
        opts={r['id']:f"#{r['id']} | {r['platform']} | {r['target']} | {r['status']}" for r in jobs}
        selected=st.selectbox('اختر التقرير',list(opts),format_func=lambda k:opts[k],key='report_job')
        job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(selected,)).fetchone())
        rows=followers_as_dicts(db,selected)
        events=events_as_dicts(db,selected)
        countries=Counter(r.get('country') or 'غير محددة' for r in rows)
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
                with st.expander('🌍 اختبار معلومات «حول الحساب» — الدولة / متصل عبر / الإيقاف'):
                    st.caption('هذه البيانات لا تأتي من مسار المتابعين. يلزم إدخال المسار الحقيقي لخدمة Get About Account في RAPIDAPI_ABOUT_PATH. ابدأ بحساب واحد؛ قد تُحسب رسوم لكل طلب. لا نعدّ غياب الحساب دليل إيقاف.')
                    if not rapid_about_path:
                        st.info('أضف RAPIDAPI_ABOUT_PATH إلى Secrets فقط بعد التأكد من مساره ومعلمات الطلب من توثيق مزودك.')
                    else:
                        about_id=st.selectbox('اختبار حساب من نتائج التقرير', [str(r['uid']) for r in rows[:20]],key=f'about_id_{selected}')
                        if st.button('فحص بيانات حول حساب واحد',key=f'about_test_{selected}'):
                            try:
                                from rapid_client import validate_path
                                result=lookup_about(about_id,rapid_key,validate_path(rapid_about_path))
                                if not any(result.values()):
                                    st.warning('لم تتضمن الاستجابة حقولًا صريحة للدولة أو طريقة الاتصال أو الإيقاف. لا يمكن تصنيفها.')
                                else:
                                    db.execute('UPDATE followers SET account_country=?,connected_via=?,suspension_status=?,about_checked_at=? WHERE job_id=? AND uid=?',
                                        (result['account_country'],result['connected_via'],result['suspension_status'],utcnow(),selected,about_id))
                                    db.commit()
                                    st.success('تم حفظ حقول About التي أعادها المزود صراحة.');st.json(result)
                                    st.rerun()
                            except RapidAPIError as e:st.error(str(e))
            st.write('#### الحسابات حسب الدولة المعلنة (تقديري)')
            st.bar_chart(pd.Series(countries).sort_values(ascending=False))
            protected=Counter(r.get('country') or 'غير محددة' for r in rows if r.get('protected')==1)
            st.write('#### الحسابات المحمية حسب الدولة')
            st.bar_chart(pd.Series(protected) if protected else pd.Series({'لا توجد حالات محمية مؤكدة':0}))
            preview=pd.DataFrame(rows[:500])
            st.dataframe(preview[['name','username','uid','country','location','account_country','connected_via','suspension_status','protected','verified']],hide_index=True,use_container_width=True)
            st.caption('يظهر أول 500 سجل للمعاينة فقط، بينما Excel يتضمن جميع السجلات المحفوظة.')
            st.download_button('⬇️ تنزيل تقرير Excel المعتمد (5 أوراق)',data=make_excel(rows,events,job),file_name=f'mirsad_{selected}.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',type='primary')
        if events:
            st.write('#### سجل الوقت والتقدم')
            st.dataframe(pd.DataFrame(events),hide_index=True,use_container_width=True)
        st.warning('الدولة في هذا التقرير مستنتجة من موقع الملف الشخصي. بيانات بلد إنشاء الحساب و«متصل عبر» والحسابات الموقوفة ليست متاحة في مسار متابعي X الرسمي ولا يجري اختلاقها.')

with s4:
    st.subheader('حالة الاتصال بحسب المنصة')
    st.markdown('**X:** يدعم RapidAPI (مع المسار الصحيح) أو واجهة X الرسمية. في وضع RapidAPI يحتاج معرف الحساب الرقمي. **إنستغرام:** استيراد CSV وتحليل وتصدير؛ لا يوجد محرك مستقل مصرح به لسحب جميع المتابعين. **تيك توك:** استيراد CSV وتحليل وتصدير؛ لا يوجد محرك مضمون لسحب جميع المتابعين.')
    st.markdown('**الصفحة About:** رابطها متاح داخل Excel للفحص اليدوي في X. لا نفترض أن حقول الدولة أو «متصل عبر» متاحة عبر X API الرسمي.')
    st.markdown('**الإنتاج:** ملفات SQLite المحلية على الاستضافات المؤقتة قد تُفقد عند إعادة تشغيل الخادم. لمهام كبيرة ومتكررة استخدم قرصًا دائمًا أو PostgreSQL مع عامل مهام مستقل قبل الاعتماد التشغيلي.')
    st.caption('لا ترسل أي مفاتيح API داخل المحادثات أو الصور. احفظها فقط في أسرار الاستضافة.')
