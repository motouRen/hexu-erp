"""Back up and restore a snapshot to an isolated database, then verify business invariants."""
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parent.parent
os.chdir(root)
sys.path.insert(0, str(root))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.conf import settings
from django.core.management import call_command
from django.db import connection

destination = root / "output" / "recovery" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
destination.mkdir(parents=True, exist_ok=False)
if connection.vendor == "sqlite":
    backup = destination / "recovered.sqlite3"
    connection.ensure_connection()
    with sqlite3.connect(backup) as restored:
        connection.connection.backup(restored)
    environment = dict(os.environ, ERP_SQLITE_PATH=str(backup), ERP_DEBUG="1", ERP_DEMO_MODE="0")
    for key in list(environment):
        if key.startswith("ERP_DB_"):
            environment.pop(key)
    subprocess.run([sys.executable, "manage.py", "verify_integrity"], check=True, env=environment)
else:
    import psycopg
    import re
    from django.db import transaction
    config = settings.DATABASES["default"]
    restored_name = "hexu_restore_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    assert re.fullmatch(r"[a-z0-9_]+", restored_name)
    connection_options = {"host": config["HOST"], "port": config["PORT"], "user": config["USER"], "password": config["PASSWORD"]}
    backup = destination / "business-snapshot.json"
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        call_command("dumpdata", output=str(backup), natural_foreign=True,
                     exclude=["contenttypes", "auth.permission"], verbosity=0)
    with psycopg.connect(dbname=config["NAME"], autocommit=True, **connection_options) as db:
        from psycopg import sql
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restored_name)))
    try:
        verify_env = dict(os.environ, ERP_DB_NAME=restored_name)
        subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True, env=verify_env)
        subprocess.run([sys.executable, "manage.py", "loaddata", str(backup)], check=True, env=verify_env)
        subprocess.run([sys.executable, "manage.py", "verify_integrity"], check=True, env=verify_env)
    finally:
        with psycopg.connect(dbname=config["NAME"], autocommit=True, **connection_options) as db:
            db.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(restored_name)))
print("Restore rehearsal passed. Private snapshot:", backup)
