"""Step 3a: introspect the live Postgres schema and render each table as a
plain-English description — this is what later gets embedded for retrieval,
and what eventually gets fed into the SQL-generation prompt.
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect

load_dotenv()

DB_URL = (
    f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
    f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)

engine = create_engine(DB_URL)
inspector = inspect(engine)


def describe_table(table_name: str) -> str:
    columns = inspector.get_columns(table_name)
    pk = inspector.get_pk_constraint(table_name).get("constrained_columns", [])
    fks = inspector.get_foreign_keys(table_name)

    fk_map = {}
    for fk in fks:
        for local_col, remote_col in zip(fk["constrained_columns"], fk["referred_columns"]):
            fk_map[local_col] = f"{fk['referred_table']}.{remote_col}"

    lines = [f"Table: {table_name}"]
    for col in columns:
        col_name = col["name"]
        col_type = str(col["type"])
        tags = []
        if col_name in pk:
            tags.append("primary key")
        if col_name in fk_map:
            tags.append(f"foreign key -> {fk_map[col_name]}")
        if not col["nullable"]:
            tags.append("not null")
        tag_str = f" ({', '.join(tags)})" if tags else ""
        lines.append(f"  - {col_name}: {col_type}{tag_str}")

    return "\n".join(lines)


if __name__ == "__main__":
    table_names = inspector.get_table_names()
    print(f"Found {len(table_names)} table(s): {table_names}\n")

    for name in table_names:
        print(describe_table(name))
        print()
