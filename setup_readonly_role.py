"""Step 7a: create the dedicated read-only Postgres role that generated SQL
will run under. This is the REAL security boundary of the pipeline -- the
Step 6 validator is software that could have a bug or be bypassed, but a
database role that simply has no write privileges cannot be talked into
writing, whatever SQL reaches it.

Idempotent: safe to re-run. If POSTGRES_RO_PASSWORD is missing from .env, a
strong random one is generated and appended to .env WITHOUT printing it, so
the secret never appears in a terminal, a chat, or a diff.
"""
import os  # standard library: read environment variables (DB host/port/admin credentials)
import secrets  # standard library: cryptographically secure random values, used for the generated password
from pathlib import Path  # standard library: path handling that works on Windows and Linux

import psycopg  # the Postgres driver, used directly here because CREATE ROLE cannot take normal bind parameters
from psycopg import sql  # safe SQL composition: quotes identifiers (role/table names) and literals (the password) correctly
from dotenv import dotenv_values, load_dotenv  # dotenv_values = read .env as a dict without touching os.environ; load_dotenv = load it into os.environ

from sql_validator import ALLOWED_TABLES  # single source of truth: the tables the validator allows are exactly the tables this role may read

ENV_PATH = Path(__file__).resolve().parent / ".env"  # absolute path to the real secrets file next to this script (never .env.example)
load_dotenv(ENV_PATH)  # load .env so os.environ has the admin credentials

STATEMENT_TIMEOUT = "5s"  # server-side cap on any single query for this role (mirrors TIMEOUT_MS in sql_executor.py)
IDLE_TX_TIMEOUT = "10s"  # kills a session that opens a transaction and then sits idle, so it cannot hold locks forever


def append_to_env(lines: list[str]) -> None:  # add KEY=VALUE lines to the end of .env without disturbing what is already there
    existing = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""  # current file content (kept in memory only, never printed)
    prefix = "" if existing == "" or existing.endswith("\n") else "\n"  # the file may lack a trailing newline; add one so we don't glue onto the last line
    with ENV_PATH.open("a", encoding="utf-8") as f:  # open in append mode: existing content is untouched
        f.write(prefix + "\n".join(lines) + "\n")  # write the new lines, each newline-terminated


env_now = dotenv_values(ENV_PATH)  # what .env currently defines (values stay in this dict, never printed)
to_append = []  # KEY=VALUE lines we still need to add
if not env_now.get("POSTGRES_RO_USER"):  # role name not configured yet
    to_append.append("POSTGRES_RO_USER=txt2sql_readonly")  # use the default name
if not env_now.get("POSTGRES_RO_PASSWORD"):  # password not configured yet
    to_append.append(f"POSTGRES_RO_PASSWORD={secrets.token_urlsafe(24)}")  # 32 URL-safe characters (no special chars that would break the DB URL); never printed
if to_append:  # something was missing
    append_to_env(to_append)  # write it to .env
    load_dotenv(ENV_PATH, override=True)  # reload so os.environ sees the new values
    print(f"Added to .env: {[line.split('=')[0] for line in to_append]} (values not shown)")  # report only the KEY NAMES, never the values

RO_USER = os.environ["POSTGRES_RO_USER"]  # the read-only role's name
RO_PASSWORD = os.environ["POSTGRES_RO_PASSWORD"]  # its password (used below, never printed)
DB_NAME = os.environ["POSTGRES_DB"]  # the database to grant access to

with psycopg.connect(  # connect as the ADMIN user (from .env), because only an admin can create roles and grant privileges
    host=os.environ["POSTGRES_HOST"],  # localhost
    port=os.environ["POSTGRES_PORT"],  # 5433
    user=os.environ["POSTGRES_USER"],  # admin user
    password=os.environ["POSTGRES_PASSWORD"],  # admin password
    dbname=DB_NAME,  # the txt2sql database
    autocommit=True,  # run each statement immediately (role/grant changes don't need one big transaction)
) as conn:  # the with-block closes the connection when done
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RO_USER,)).fetchone() is not None  # does the role already exist?
    verb = "ALTER" if exists else "CREATE"  # re-running updates the existing role instead of failing
    conn.execute(  # create/alter the role itself
        sql.SQL(  # sql.SQL builds the statement safely from parts
            "{verb} ROLE {role} WITH LOGIN PASSWORD {pw} "  # LOGIN = may connect; the password is inserted as a properly quoted literal
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "  # explicitly strip every powerful attribute
            "CONNECTION LIMIT 10"  # at most 10 simultaneous connections for this role
        ).format(verb=sql.SQL(verb), role=sql.Identifier(RO_USER), pw=sql.Literal(RO_PASSWORD))  # fill the placeholders with safely quoted values
    )
    conn.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(sql.Identifier(RO_USER), sql.Literal(STATEMENT_TIMEOUT)))  # any query longer than this is cancelled by the server
    conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(sql.Identifier(RO_USER)))  # every transaction starts read-only (an extra layer on top of having no write privileges)
    conn.execute(sql.SQL("ALTER ROLE {} SET idle_in_transaction_session_timeout = {}").format(sql.Identifier(RO_USER), sql.Literal(IDLE_TX_TIMEOUT)))  # drop sessions that sit idle inside a transaction

    conn.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DB_NAME)))  # nobody may create temp tables by default
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(DB_NAME), sql.Identifier(RO_USER)))  # allow the role to connect to this database
    conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))  # nobody may create objects in the public schema by default (already the default on Postgres 15+, made explicit)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(RO_USER)))  # allow the role to look up objects in the public schema (needed before any table grant works)
    conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(sql.Identifier(RO_USER)))  # start from zero table privileges so a re-run removes anything granted by mistake earlier
    conn.execute(  # now grant exactly what is needed, and nothing more
        sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(  # SELECT only: no INSERT / UPDATE / DELETE / TRUNCATE
            sql.SQL(", ").join(sql.Identifier(t) for t in sorted(ALLOWED_TABLES)),  # the same 4 tables the validator allowlist permits
            sql.Identifier(RO_USER),  # granted to the read-only role
        )
    )

    print(f"Role '{RO_USER}' {'updated' if exists else 'created'}.")  # confirm what happened (role name only)
    print("Table privileges now held by the role (from information_schema):")  # show the effective grants as proof
    for table_name, privilege in conn.execute(  # ask the database what this role can actually do
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants "  # one row per (table, privilege) granted
        "WHERE grantee = %s ORDER BY table_name, privilege_type",  # only this role's grants, sorted
        (RO_USER,),  # the role name as a bind parameter
    ):
        print(f"  {table_name}: {privilege}")  # e.g. "customers: SELECT"
