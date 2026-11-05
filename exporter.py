"""Excel output: complete URL text (not hyperlinks); permanent IDs as text."""
import io
from collections import Counter,defaultdict
from datetime import datetime
import xlsxwriter
from report_fields import account_links, account_status, connected_via, elapsed_metrics, fmt_duration
from filtering import exportable_rows

HEADERS=['الاسم','اليوزر','المعرف الدائم','رابط المعرف الدائم','رابط الحساب','رابط حول الحساب','الموقع المعلن','الدولة (من الموقع المعلن)','الحساب محمي','موثق','عدد المتابعين','عدد المتابَعين','وقت المعالجة UTC','الدولة حسب حول الحساب','متصل عبر','حالة الحساب']

def clean_cell(value):
    if value is None:return ''
    value=str(value)
    # Prevent formula injection in spreadsheet viewers.
    if value.lstrip().startswith(('=','+','-','@','\t','\r','\n')):return "'"+value
    return value

def make_excel(rows,events,job):
    rows=exportable_rows(rows)
    output=io.BytesIO()
    book=xlsxwriter.Workbook(output,{'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False})
    book.set_properties({'title':'تقرير الحسابات حسب الدولة','comments':'الدولة حسب معلومات حول الحساب'})
    title=book.add_format({'bold':True,'font_color':'white','bg_color':'17384A','align':'right','valign':'vcenter','text_wrap':True,'border':0})
    plain=book.add_format({'align':'right','valign':'vcenter'})
    textfmt=book.add_format({'num_format':'@','align':'left'})
    url_text=book.add_format({'num_format':'@','align':'left','valign':'vcenter','font_color':'#17384A'})
    percent=book.add_format({'num_format':'0.0%','align':'center'})
    def sheet(name,headers,widths=None):
        ws=book.add_worksheet(name)
        ws.right_to_left();ws.freeze_panes(1,0);ws.set_row(0,30)
        for idx,h in enumerate(headers):ws.write_string(0,idx,h,title)
        for idx,width in enumerate(widths or []):ws.set_column(idx,idx,width)
        return ws
    w=sheet('المتابعون',HEADERS,[25,21,25,49,39,44,27,26,16,12,18,19,25,24,20,22])
    distribution=Counter()
    protected=defaultdict(lambda:[0,0])
    for i,r in enumerate(rows,1):
        uid=str(r['uid']);username=str(r.get('username') or '')
        country=r['account_country'];distribution[country]+=1
        protected[country][1]+=1
        if r.get('protected')==1:protected[country][0]+=1
        perm_url, profile_url, about_url = account_links(r)
        cols=[r.get('name'),('@'+username if username else ''),uid,
              perm_url,profile_url,about_url,
              r.get('location'),country,
              'نعم' if r.get('protected')==1 else ('لا' if r.get('protected')==0 else 'غير متاح'),
              'نعم' if r.get('verified')==1 else ('لا' if r.get('verified')==0 else 'غير متاح'),
              r.get('followers_count'),r.get('following_count'),r.get('fetched_at'),
              r.get('account_country') or 'لم يتحقق',connected_via(r),account_status(r)]
        for j,v in enumerate(cols):
            if j in (3,4,5):
                # Visible complete URL, stored as plain text, with no Excel hyperlink relationship.
                w.write_string(i,j,clean_cell(v),url_text)
            elif j==1:w.write_string(i,j,('@'+username if username else ''),textfmt)
            elif j==2:w.write_string(i,j,uid,textfmt)
            elif j in (10,11) and v is not None:
                try:w.write_number(i,j,int(v),plain)
                except (ValueError,TypeError):w.write_string(i,j,clean_cell(v),plain)
            else:w.write_string(i,j,clean_cell(v),plain)
    w.autofilter(0,0,len(rows),len(HEADERS)-1)
    geo=sheet('تحليل الدول',['الدولة حسب حول الحساب','عدد الحسابات','النسبة المئوية'],[28,19,22])
    all_count=max(1,len(rows))
    for idx,(country,n) in enumerate(distribution.most_common(),1):
        geo.write_string(idx,0,country);geo.write_number(idx,1,n);geo.write_number(idx,2,n/all_count,percent)
    if distribution:
        chart=book.add_chart({'type':'bar'});chart.add_series({'name':'عدد الحسابات','categories':['تحليل الدول',1,0,len(distribution),0],'values':['تحليل الدول',1,1,len(distribution),1]})
        chart.set_title({'name':'التوزيع حسب دولة الحساب'});chart.set_legend({'none':True});chart.set_size({'width':700,'height':390});geo.insert_chart('E2',chart)
    pr=sheet('الحسابات المحمية',['الدولة حسب حول الحساب','الحسابات المحمية','كل الحسابات','نسبة المحمية'],[29,25,21,22])
    for i,(country,(p,total)) in enumerate(sorted(protected.items(),key=lambda kv:-kv[1][0]),1):
        pr.write_string(i,0,country);pr.write_number(i,1,p);pr.write_number(i,2,total);pr.write_number(i,3,p/total if total else 0,percent)
    ev=sheet('الخط الزمني',['الوقت UTC','الحدث','تمت معالجته','حسابات فريدة','التفاصيل'],[28,18,19,18,64])
    for i,item in enumerate(events,1):
        for j,key in enumerate(('at','kind','scanned','unique_count','details')):
            value=item.get(key)
            if j in (2,3):ev.write_number(i,j,int(value or 0))
            else:ev.write_string(i,j,clean_cell(value))
    metrics=elapsed_metrics(job, events)
    timeline_row=len(events)+3
    ev.write_string(timeline_row,0,'مدة طلبات سحب المتابعين',title)
    ev.write_string(timeline_row,1,fmt_duration(metrics['api_seconds']),plain)
    ev.write_string(timeline_row+1,0,'مدة المهمة منذ الإنشاء إلى آخر تحديث',title)
    ev.write_string(timeline_row+1,1,fmt_duration(metrics['wall_seconds']),plain)
    ev.write_string(timeline_row+2,0,'مدة إثراء About الدقيقة',title)
    ev.write_string(timeline_row+2,1,'غير مسجلة منفصلًا في النسخة الحالية',plain)
    ev.write_string(timeline_row+3,0,'متوسط مدة طلب سحب صفحة',title)
    ev.write_string(timeline_row+3,1,fmt_duration(metrics['seconds_per_page']),plain)
    book.close();return output.getvalue()
