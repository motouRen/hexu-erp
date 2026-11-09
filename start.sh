#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -f .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py init_demo
printf '\nLocal ERP: http://127.0.0.1:8765/\nCredentials: LOCAL_ACCESS.txt\n'
exec .venv/bin/python manage.py runserver 127.0.0.1:8765
