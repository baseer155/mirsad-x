"""Optional independent ONE-SHOT worker on a persistent host.

Usage: python worker.py --once or python worker.py --loop --interval 5
Run only one worker process against a durable SQLite volume. DO NOT run on
Streamlit Community Cloud's ephemeral filesystem expecting durable jobs.
"""
import argparse
import os
import time
import sqlite3
from core import connect
from multi_accounts import get_groups, tick


def cycle(db):
    key = os.environ.get('RAPIDAPI_KEY', '')
    path = os.environ.get('RAPIDAPI_FOLLOWERS_PATH', '')
    if not (key and path):
        raise RuntimeError('يجب ضبط RAPIDAPI_KEY و RAPIDAPI_FOLLOWERS_PATH عبر متغيرات البيئة')
    result = []
    for group in get_groups(db):
        if group['status'] != 'قيد التشغيل':
            continue
        try:
            result.append((group['id'], tick(db, group['id'], rapid_key=key, followers_path=path,
                about_path=os.environ.get('RAPIDAPI_ABOUT_PATH', '/about-account'),
                profiles_path=os.environ.get('RAPIDAPI_PROFILES_PATH', '/get-users'))))
        except sqlite3.OperationalError as exc:
            # Busy DB: leave the job resumable rather than falsely marking it successful.
            result.append((group['id'], {'state': 'paused', 'message': type(exc).__name__}))
    return result


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--once', action='store_true')
    mode.add_argument('--loop', action='store_true')
    parser.add_argument('--interval', type=float, default=5)
    args = parser.parse_args()
    if args.interval < 1:
        parser.error('--interval must be >= 1')
    db = connect(os.environ.get('MIRSAD_DB', 'mirsad.sqlite3'))
    try:
        while True:
            for group_id, result in cycle(db):
                print(f'group={group_id} state={result.get("state")}', flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        db.close()


if __name__ == '__main__':
    main()
