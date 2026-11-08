import tempfile
import unittest
from pathlib import Path
from core import connect, create_job
from auto_enrich import set_policy, stats
from readiness import inspect_job, inspect_group
from multi_accounts import create_group

class ReportReadinessTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.db=connect(str(Path(self.folder.name) / 'test.db'))
        self.job=create_job(self.db, 'X','example','123')
        set_policy(self.db, self.job, True, 50, 0, 0)

    def tearDown(self):
        self.db.close()
        self.folder.cleanup()

    def follower(self, uid, username='user', country='Saudi Arabia'):
        self.db.execute('INSERT INTO followers(job_id,uid,username,account_country) VALUES(?,?,?,?)',
                        (self.job,uid,username,country))
        self.db.commit()

    def end(self):
        self.db.execute("UPDATE jobs SET status='مكتمل' WHERE id=?",(self.job,))
        self.db.commit()

    def test_no_final_when_fetch_not_finished(self):
        self.assertFalse(inspect_job(self.db,self.job)['approved'])

    def test_no_final_when_about_queued(self):
        self.follower('500')
        self.end()
        result=inspect_job(self.db,self.job)
        self.assertFalse(result['approved'])
        self.assertEqual(result['about_pending'],1)

    def test_final_only_after_about_done(self):
        self.follower('500')
        self.end()
        stats(self.db,self.job)
        self.db.execute("UPDATE about_attempts SET state='done' WHERE job_id=?",(self.job,))
        self.db.commit()
        self.assertTrue(inspect_job(self.db,self.job)['approved'])

    def test_no_final_if_exhausted(self):
        self.follower('500')
        self.end()
        stats(self.db,self.job)
        self.db.execute("UPDATE about_attempts SET state='exhausted' WHERE job_id=?",(self.job,))
        self.db.commit()
        self.assertFalse(inspect_job(self.db,self.job)['approved'])

    def test_group_remains_provisional_until_every_target_is_done(self):
        gid=create_group(self.db,'اختبار',[('account1','456'),('account2','789')],
                         goal=100,page_cap=50,page_size=20,about_budget=50)
        self.assertFalse(inspect_group(self.db,gid)['approved'])

if __name__=='__main__': unittest.main()
