#!/bin/sh
set -eu
python scripts/database.py upgrade
python scripts/database.py upgrade
python scripts/database.py check
python scripts/database.py ping
exec python -m pytest -q -p no:cacheprovider --basetemp=/tmp/codatau-tests --tb=short
