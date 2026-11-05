"""Verify both Supabase schema entrypoints and the complete web flow on local,
disposable PostgreSQL. Requires initdb and pg_ctl on PATH. No cloud connection.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
from tests.web_flow import free_port, main as web_flow


def main():
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        raise SystemExit("Install PostgreSQL server tools to run this optional check.")
    backend = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="ube-postgres-test-") as folder:
        port = free_port()
        data = str(Path(folder) / "data")
        subprocess.run([initdb, "-D", data, "--auth=trust", "--username=ube_test"], check=True, capture_output=True)
        subprocess.run([pg_ctl, "-D", data, "-l", str(Path(folder) / "postgres.log"),
                        "-o", f"-h 127.0.0.1 -p {port} -k {folder}", "-w", "start"], check=True, capture_output=True)
        base = f"postgresql://ube_test@127.0.0.1:{port}"
        try:
            with psycopg.connect(base + "/postgres", autocommit=True) as db:
                db.execute("CREATE ROLE anon")
                db.execute("CREATE ROLE authenticated")
                db.execute("CREATE DATABASE schema_sql")
                db.execute("CREATE DATABASE web_test")
                db.execute("CREATE DATABASE legacy_test")
            with psycopg.connect(base + "/schema_sql", autocommit=True) as db:
                db.execute((backend / "supabase/migrations/202611030001_inspection_schema.sql").read_text())
                assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001_inspection_schema"
                # Re-running the SQL Editor migration is safe.
                db.execute((backend / "supabase/migrations/202611030001_inspection_schema.sql").read_text())
                for filename in ("202611030002_optional_inspection_lot.sql", "202611050003_human_defects.sql", "202611050004_geometry_measurements.sql", "202611050005_automatic_processing.sql"):
                    db.execute((backend / "supabase/migrations" / filename).read_text())
                assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0005_automatic_processing"
            with psycopg.connect(base + "/legacy_test", autocommit=True) as db:
                db.execute("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(80) NOT NULL UNIQUE, "
                           "password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL, is_active BOOLEAN NOT NULL)")
                db.execute("INSERT INTO users VALUES ('legacy', 'old-user', 'hash', 'admin', true)")
            env = {**os.environ, "DATABASE_URL": base + "/legacy_test", "JWT_SECRET_KEY": "postgres-migration-test-secret-not-production"}
            subprocess.run([sys.executable, "-m", "app.migrate"], cwd=backend, env=env, check=True, capture_output=True)
            with psycopg.connect(base + "/legacy_test") as db:
                assert db.execute("SELECT username, full_name FROM users").fetchone() == ("old-user", "")
            # Next.js -> FastAPI -> actual Postgres, with isolated image storage/mock AI.
            web_flow(base + "/web_test")
            for name in ("schema_sql", "web_test", "legacy_test"):
                with psycopg.connect(base + "/" + name, autocommit=True) as db:
                    rows = db.execute("SELECT relname, relrowsecurity FROM pg_class JOIN pg_namespace ON pg_namespace.oid=pg_class.relnamespace "
                                      "WHERE nspname='public' AND relkind='r'").fetchall()
                    assert len(rows) == 9 and all(enabled for _, enabled in rows), rows
                    assert not db.execute("SELECT has_table_privilege('anon','public.users','SELECT')").fetchone()[0]
                    assert not db.execute("SELECT has_table_privilege('authenticated','public.users','SELECT')").fetchone()[0]
                    db.execute("SET ROLE anon")
                    try:
                        db.execute("SELECT password_hash FROM public.users")
                        raise AssertionError("anon could read password hashes")
                    except psycopg.errors.InsufficientPrivilege:
                        pass
            print("PASS: PostgreSQL schema SQL + Alembic legacy upgrade + RLS/grants + full web integration")
        finally:
            subprocess.run([pg_ctl, "-D", data, "-m", "fast", "-w", "stop"], check=True, capture_output=True)


if __name__ == "__main__":
    main()
