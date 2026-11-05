import tempfile
from pathlib import Path
from core import connect,create_job,save_page
from auto_enrich import set_policy,stats
from auto_runner import step,snapshot

with tempfile.TemporaryDirectory() as tmp:
    db=connect(str(Path(tmp)/'test.sqlite'))
    job=create_job(db,'X','sample','123456')
    set_policy(db,job,True,3,0.25,0.75)
    calls={'pages':0,'about':[]}
    def fake_fetch(db,job_id,key,path,page_limit,unique_limit,count,callback=None):
        calls['pages']+=1
        users=[{'id':str(900000000000000000+calls['pages']*100+i),
                'username':f'user_{calls["pages"]}_{i}', 'name':f'User {i}'} for i in range(2)]
        save_page(db,job_id,users,'next'+str(calls['pages']),0.1)
        return 1
    def fake_about(username,key,path):
        calls['about'].append(username)
        return {'account_country':'Saudi Arabia','connected_via':None,'suspension_status':None}
    r1=step(db,job,rapid_key='fake',followers_path='/followers',about_path='/about-account',goal=6,
            page_limit=1,about_batch=2,follower_fetcher=fake_fetch,about_fetcher=fake_about)
    assert r1['snapshot']['unique']==2 and r1['about_attempted']==2
    r2=step(db,job,rapid_key='fake',followers_path='/followers',about_path='/about-account',goal=6,
            page_limit=1,about_batch=2,follower_fetcher=fake_fetch,about_fetcher=fake_about)
    assert r2['snapshot']['unique']==4 and r2['snapshot']['about']['used_requests']==3
    r3=step(db,job,rapid_key='fake',followers_path='/followers',about_path='/about-account',goal=6,
            page_limit=1,about_batch=2,follower_fetcher=fake_fetch,about_fetcher=fake_about)
    assert r3['snapshot']['unique']==6 and r3['state']=='paused' and len(calls['about'])==3
    assert stats(db,job)['remaining_requests']==0
    print('PASS: automatic successive cycles; budget cap; unique goals; checkpoints')
