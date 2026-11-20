"""Portable migration/check commands; never prints connection credentials."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app
from app.extensions import db
from flask_migrate import upgrade, check
from sqlalchemy import text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['upgrade', 'check', 'ping'])
    args = parser.parse_args()
    app = create_app(os.environ.get('APP_ENV', 'production'))
    with app.app_context():
        if args.command == 'upgrade':
            upgrade(directory=str(Path(__file__).resolve().parents[1] / 'migrations'))
        elif args.command == 'check':
            check(directory=str(Path(__file__).resolve().parents[1] / 'migrations'))
        else:
            db.session.execute(text('SELECT 1'))
        print(f'Base de datos: {args.command} correcto.')


if __name__ == '__main__':
    main()
