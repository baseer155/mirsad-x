"""Memory-backed Excel output. User identifiers stay strings; links are clickable."""
import io
from collections import Counter,defaultdict
from datetime import datetime
import xlsxwriter
from content_status import posts_status

HEADERS=['الاسم','اليوزر','المعرف الدائم','رابط المعرف الدائم','رابط الحساب','رابط حول الحساب','الموقع المعلن','الدولة (من الموقع المعلن)','الحساب محمي','موثق','عدد المتابعين','عدد المتابَعين','وقت المعالجة UTC','الدولة حسب حول الحساب','متصل عبر','حالة الإيقاف','حالة المنشورات']

def clean_cell(value):
    if value is None:return ''
    value=str(value)
    # Prevent formula injection in spreadsheet viewers.
    if value.lstrip().startswith(('=','+','-','@','\t','\r','\n')):return "'"+value
    return value

def make_excel(rows,events,job):
    output=io.BytesIO()
    book=xlsxwriter.Workbook(output,{'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False})
    book.set_properties({'title':'مِرصاد | تقرير الحسابات','comments':'تقديرات الدول من الموقع المعلن، وليست بلد إنشاء الحساب'})
    title=book.add_format({'bold':True,'font_color':'white','bg_color':'17384A','align':'right','valign':'vcenter','text_wrap':True,'border':0})
    plain=book.add_format({'align':'right','valign':'vcenter'})
    textfmt=book.add_format({'num_format':'@','align':'left'})
    link=book.add_format({'font_color':'#0877BA','underline':1,'align':'left'})
    percent=book.add_format({'num_format':'0.0%','align':'center'})
    def sheet(name,headers,widths=None):
        ws=book.add_worksheet(name)
        ws.right_to_left();ws.freeze_panes(1,0);ws.set_row(0,30)
        for idx,h in enumerate(headers):ws.write_string(0,idx,h,title)
        for idx,width in enumerate(widths or []):ws.set_column(idx,idx,width)
        return ws
    w=sheet('المتابعون',HEADERS,[25,21,25,49,39,44,27,26,16,12,18,19,25,24,20,19,47])
    distribution=Counter()
    protected=defaultdict(lambda:[0,0])
    for i,r in enumerate(rows,1):
        uid=str(r['uid']);username=str(r.get('username') or '')
        country=r.get('country') or 'غير محددة';distribution[country]+=1
        protected[country][1]+=1
        if r.get('protected')==1:protected[country][0]+=1
        cols=[r.get('name'),('@'+username if username else ''),uid,
              'https://x.com/i/user/'+uid,
              'https://x.com/'+username if username else '',
              'https://x.com/'+username+'/about' if username else '',
              r.get('location'),country,
              'نعم' if r.get('protected')==1 else ('لا' if r.get('protected')==0 else 'غير متاح'),
              'نعم' if r.get('verified')==1 else ('لا' if r.get('verified')==0 else 'غير متاح'),
              r.get('followers_count'),r.get('following_count'),r.get('fetched_at'),
              r.get('account_country') or 'لم يتحقق',r.get('connected_via') or 'لم يتحقق',r.get('suspension_status') or 'لم يتحقق',posts_status(r)]
        for j,v in enumerate(cols):
            if j in (3,4,5) and v:
                w.write_url(i,j,v,link,'فتح الرابط')
            elif j==2:w.write_string(i,j,uid,textfmt)
            elif j in (10,11) and v is not None:
                try:w.write_number(i,j,int(v),plain)
                except (ValueError,TypeError):w.write_string(i,j,clean_cell(v),plain)
            else:w.write_string(i,j,clean_cell(v),plain)
    w.autofilter(0,0,len(rows),len(HEADERS)-1)
    geo=sheet('تحليل الدول',['الدولة (تقديرية)','عدد الحسابات','النسبة المئوية'],[28,19,22])
    all_count=max(1,len(rows))
    for idx,(country,n) in enumerate(distribution.most_common(),1):
        geo.write_string(idx,0,country);geo.write_number(idx,1,n);geo.write_number(idx,2,n/all_count,percent)
    if distribution:
        chart=book.add_chart({'type':'bar'});chart.add_series({'name':'عدد الحسابات','categories':['تحليل الدول',1,0,len(distribution),0],'values':['تحليل الدول',1,1,len(distribution),1]})
        chart.set_title({'name':'التوزيع حسب الموقع المعلن'});chart.set_legend({'none':True});chart.set_size({'width':700,'height':390});geo.insert_chart('E2',chart)
    pr=sheet('الحسابات المحمية',['الدولة (تقديرية)','الحسابات المحمية','كل الحسابات','نسبة المحمية'],[29,25,21,22])
    for i,(country,(p,total)) in enumerate(sorted(protected.items(),key=lambda kv:-kv[1][0]),1):
        pr.write_string(i,0,country);pr.write_number(i,1,p);pr.write_number(i,2,total);pr.write_number(i,3,p/total if total else 0,percent)
    ev=sheet('الخط الزمني',['الوقت UTC','الحدث','تمت معالجته','حسابات فريدة','التفاصيل'],[28,18,19,18,64])
    for i,item in enumerate(events,1):
        for j,key in enumerate(('at','kind','scanned','unique_count','details')):
            value=item.get(key)
            if j in (2,3):ev.write_number(i,j,int(value or 0))
            else:ev.write_string(i,j,clean_cell(value))
    notes=sheet('المنهجية والقيود',['البند','التفاصيل'],[35,105])
    caveats=[
      ('الحساب المستهدف',job['target']),('المنصة',job['platform']),('آخر حالة',job['status']),
      ('السجلات الفريدة',str(len(rows))),('المعالج / التكرارات',f"{job['scanned']} / {job['duplicates']}"),
      ('الوقت المسجل بالثواني',str(round(job['elapsed_seconds'],1))),
      ('الدولة','تصنيف تخميني من الموقع المعلن في الملف الشخصي فقط، وليس بلد المنشأ أو الجنسية.'),
      ('حول الحساب','الروابط موجودة للفحص اليدوي؛ بيانات About ليست متاحة ضمن مسار متابعي X الرسمي.'),
      ('الحساب الموقوف','لا يتم تصنيف الحساب كموقوف دون حالة صريحة من مصدر موثوق.'),
      ('معرف المستخدم','مخزن في Excel بصيغة نص للحفاظ على جميع الأرقام.'),
      ('الاكتمال','القدرة على سحب جميع الحسابات تتوقف على صلاحيات X API وحدود الخدمة.'),
      ('المصدر','RapidAPI عند ضبطه، أو X API الرسمي، أو CSV. حقول الدولة حسب حول الحساب/متصل عبر/الإيقاف تتطلب استجابة مصدر إضافي صريحة.')]
    for i,(a,b) in enumerate(caveats,1):notes.write_string(i,0,a);notes.write_string(i,1,clean_cell(b))
    book.close();return output.getvalue()
