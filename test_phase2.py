import os, tempfile, unittest
from core import connect
from multi_accounts import parse_targets,resolve_targets,create_group,members
from readiness import inspect_group

class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=connect(os.path.join(self.tmp.name,'test.sqlite3'))
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def test_handle_only_and_legacy(self):
        rows,errors=parse_targets('@alpha\nhttps://x.com/beta\n@gamma,123456')
        self.assertEqual(errors,[])
        self.assertEqual(rows,[('alpha',None),('beta',None),('gamma','123456')])
    def test_duplicates(self):
        rows,errors=parse_targets('@alpha\n@ALPHA')
        self.assertEqual(len(rows),1)
        self.assertTrue(errors)
    def test_verified_and_cached(self):
        mapping={'alpha':('111',51),'beta':('222',0)}
        def fetcher(handle,key,path):
            uid,total=mapping[handle]
            return {'id':uid,'username':handle,'followers_count':total}
        resolved,errors=resolve_targets([('alpha',None),('beta',None)],'dummy',fetcher=fetcher)
        self.assertEqual(errors,[])
        gid=create_group(self.db,'تجربة',resolved,goal=1000000,page_cap=1000,page_size=20)
        rows=members(self.db,gid)
        self.assertEqual([r['follower_total'] for r in rows],[51,0])
        self.assertEqual([r['target_uid'] for r in rows],['111','222'])
        self.assertFalse(inspect_group(self.db,gid)['approved'])
    def test_mismatched_id(self):
        found,errors=resolve_targets([('alpha','999')],'dummy',fetcher=lambda *args:{'id':'111','username':'alpha','followers_count':10})
        self.assertEqual(found,[])
        self.assertTrue(errors)
    def test_account_not_found(self):
        found,errors=resolve_targets([('alpha',None)],'dummy',fetcher=lambda *args:{'id':'111','username':'other'})
        self.assertEqual(found,[])
        self.assertTrue(errors)

if __name__=='__main__':unittest.main()
