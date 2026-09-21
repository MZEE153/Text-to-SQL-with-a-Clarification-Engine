"""Step 3a: introspect the live Postgres schema and render each table as a
plain-English description — this is what later gets embedded for retrieval,
and what eventually gets fed into the SQL-generation prompt.
"""
import os  # standard library: read environment variables (the DB credentials)
from dotenv import load_dotenv  # reads KEY=VALUE lines from the .env file into environment variables
from sqlalchemy import create_engine, inspect  # create_engine = DB connection factory; inspect = read a live database's structure (tables, columns, keys)

load_dotenv()  # load .env now so the POSTGRES_* variables below exist

DB_URL = (  # SQLAlchemy connection string, built from .env values so no credentials live in code
    f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"  # dialect+driver (Postgres via psycopg 3), then user:password
    f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"  # @host:port/database -> localhost:5433/txt2sql (5433 = port Docker maps to Postgres)
)

engine = create_engine(DB_URL)  # connection factory (lazy: doesn't connect until first use)
inspector = inspect(engine)  # connects now and returns an Inspector that can read schema metadata from the live database


def describe_table(table_name: str) -> str:  # turns one table's structure into a plain-English text block (this text is what gets embedded later)
    columns = inspector.get_columns(table_name)  # list of dicts (name, type, nullable, ...), one per column
    pk = inspector.get_pk_constraint(table_name).get("constrained_columns", [])  # names of the primary-key column(s); [] if the table has none
    fks = inspector.get_foreign_keys(table_name)  # list of foreign-key definitions (which local column points at which remote table.column)

    fk_map = {}  # will map local column name -> "remote_table.remote_column"
    for fk in fks:  # one entry per foreign-key constraint
        for local_col, remote_col in zip(fk["constrained_columns"], fk["referred_columns"]):  # pair each local column with the remote column it references
            fk_map[local_col] = f"{fk['referred_table']}.{remote_col}"  # e.g. customer_id -> customers.customer_id

    lines = [f"Table: {table_name}"]  # the output starts with a header line naming the table
    for col in columns:  # one output line per column
        col_name = col["name"]  # the column's name
        col_type = str(col["type"])  # SQLAlchemy type object -> readable text, e.g. NUMERIC(12, 2)
        tags = []  # extra facts about this column (primary key / foreign key / not null)
        if col_name in pk:  # is this column part of the primary key?
            tags.append("primary key")  # record that fact
        if col_name in fk_map:  # is this column a foreign key?
            tags.append(f"foreign key -> {fk_map[col_name]}")  # record it, including which table.column it points at
        if not col["nullable"]:  # is the column NOT NULL?
            tags.append("not null")  # record that fact
        tag_str = f" ({', '.join(tags)})" if tags else ""  # "(primary key, not null)" or an empty string when there are no tags
        lines.append(f"  - {col_name}: {col_type}{tag_str}")  # e.g. "  - order_id: INTEGER (primary key, not null)"

    return "\n".join(lines)  # join all lines into one multi-line string


if __name__ == "__main__":  # only runs when executed directly (python schema_introspection.py), not when imported
    table_names = inspector.get_table_names()  # names of every table in the database
    print(f"Found {len(table_names)} table(s): {table_names}\n")  # summary line: how many tables and their names

    for name in table_names:  # go through each table
        print(describe_table(name))  # print its plain-English description
        print()  # blank line between tables
