"""Create db/gtm_pipeline.db from db/schema.sql (safe to re-run) and print the schema."""
from pipeline_db import DB_PATH, init_db

if __name__ == "__main__":
    conn = init_db()
    print(f"Initialized {DB_PATH}")
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
        print(f"\n{name} ({len(cols)} columns)")
        for c in cols:
            pk = " PK" if c["pk"] else ""
            print(f"  {c['name']:<24} {c['type'] or ''}{pk}")
    conn.close()
