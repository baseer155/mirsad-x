"""Offline restoration helper. Stop Streamlit/worker before switching DB path."""
import argparse
from ops_backup import restore_offline


def main():
    parser = argparse.ArgumentParser(description='Restore SQLite backup into a NEW database file')
    parser.add_argument('backup', help='Existing .sqlite3 backup')
    parser.add_argument('destination', help='New database path; must not exist')
    parser.add_argument('--confirm', action='store_true', help='Acknowledge offline restoration')
    args = parser.parse_args()
    result = restore_offline(args.backup, args.destination, confirmed=args.confirm)
    print('Restored successfully to:', result)


if __name__ == '__main__':
    main()
