from io import BytesIO
from zipfile import ZipFile
from collections import Counter
from docx import Document
from pptx import Presentation
from filtering import exportable_rows
from report_export import make_word,make_powerpoint
from exporter import make_excel
rows=[
{'name':'اسم مؤكد','username':'confirmed_user','uid':'1234567890123456789','account_country':'Saudi Arabia','country':'غير محدد','protected':1},
{'name':'اسم مستبعد','username':'excluded_user','uid':'9999999999999999999','account_country':'غير متاح من المصدر','country':'Yemen'},
{'name':'اسم مستبعد2','username':'excluded_user2','uid':'9999999999999999998','account_country':None},
{'name':'اسم مؤكد 2','username':'ok_user2','uid':'2222222222222222222','account_country':'Yemen','protected':0}]
job={'target':'@target_account','platform':'X','status':'done','pages':4,'elapsed_seconds':13,'scanned':100,'duplicates':0}
events=[]
valid=exportable_rows(rows)
assert len(valid)==2,valid
w=make_word(rows,events,job);p=make_powerpoint(rows,events,job);x=make_excel(rows,events,job)
d=Document(BytesIO(w));slides=Presentation(BytesIO(p))
wtext='\n'.join([pp.text for pp in d.paragraphs]+[c.text for t in d.tables for r in t.rows for c in r.cells])
ptext='\n'.join(sh.text for sl in slides.slides for sh in sl.shapes if sh.has_text_frame)
with ZipFile(BytesIO(x)) as z:
    texts=' '.join(z.read(n).decode('utf-8') for n in z.namelist() if n.endswith('.xml'))
for label,txt in [('word',wtext),('powerpoint',ptext),('excel',texts)]:
    for banned in ('مِرصاد','مرصاد','المنهجية والقيود','excluded_user','9999999999999999999','اسم مستبعد'):
        assert banned not in txt,(label,banned)
    assert 'confirmed_user' in txt or label=='excel',(label,'confirmed user missing')
assert 'Saudi Arabia' in wtext and 'Yemen' in wtext
assert len(slides.slides)>=5
for ext,data in [('docx',w),('pptx',p),('xlsx',x)]:
    with open('/mnt/data/mirsad_v10_clean_export/نموذج_تقرير_نهائي.'+ext,'wb') as f:f.write(data)
print('OK rows',len(valid),'word bytes',len(w),'slides',len(slides.slides),'excel bytes',len(x))
