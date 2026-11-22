#!/bin/sh
set -eu
# One instance for this demo. Fail deployment if migrations fail.
python scripts/database.py upgrade
exec gunicorn --bind "0.0.0.0:${PORT:-5000}" --workers 1 --threads 2 --timeout 180 wsgi:app
