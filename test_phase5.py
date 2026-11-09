"""Offline functional checks: no paid API requests and no actual concurrency load."""
import os
import sqlite3
import tempfile
from pathlib import Path
from ops_backup import backup_bytes, restore_offline, assert_integrity
from ops_health import db_health, security_flags
from core import connect, create_job


def run():
    with tempfile.TemporaryDirectory() as directory:
        src=Path(directory)/'original.sqlite3'
        db=connect(str(src))
        counts=[]
        for size in (10, 50, 100):
            for n in range(size):
                create_job(db, 'X', f'target_{size}_{n}', str(size*1000+n))
            counts.append(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0])
            assert counts[-1] == sum((10,50,100)[:len(counts)])
        snapshot=db_health(db)
        assert snapshot['ok'] and snapshot['counts']['jobs']==160
        content=backup_bytes(db)
        assert content[:16]==b'SQLite format 3\x00'
        backup=Path(directory)/'snapshot.sqlite3'
        backup.write_bytes(content)
        destination=Path(directory)/'restored.sqlite3'
        try:
            restore_offline(backup, destination)
        except PermissionError:
            pass
        else:
            raise AssertionError('Restore without confirmation must fail')
        restore_offline(backup,destination,confirmed=True)
        assert_integrity(sqlite3.connect(str(destination)))
        assert sqlite3.connect(str(destination)).execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==160
        try:
            restore_offline(backup, destination, confirmed=True)
        except FileExistsError:
            pass
        else:
            raise AssertionError('Restore must never overwrite an existing database')
        create_job(db, 'X', 'new', '999')
        db.commit()
        assert sqlite3.connect(str(destination)).execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==160
        assert db_health(db)['counts']['jobs']==161
        db.close()
    flags=security_flags(password_set=False,api_key_set=False,database_path='mirsad.sqlite3',external_worker=False)
    assert len(flags)==4 and not any(passed for _,passed,_ in flags)
    print('PASS: 10/50/100 synthetic job records (no API); database integrity; online backup; offline restore')
    print('PASS: restore confirmation, refuse overwrite, snapshot isolation, configuration flags')

if __name__=='__main__':
    run()
