"""Run from a persistent scheduler, e.g. cron daily. NO Streamlit requirement."""
import argparse
from contextlib import closing
from core import connect
from ops_retention import clean_due


def main():
    p=argparse.ArgumentParser(description='Mirsad safe retention sweep')
    p.add_argument('--db',required=True)
    p.add_argument('--execute',action='store_true',help='delete only pre-approved archived rows; default dry-run')
    args=p.parse_args()
    with closing(connect(args.db)) as db:
        result=clean_due(db,dry_run=not args.execute)
    print('eligible={eligible} cleaned={cleaned} followers_deleted={followers_deleted}'.format(**result))

if __name__=='__main__':main()
