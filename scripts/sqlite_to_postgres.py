"""把 latin_library.db 的数据导入 Supabase Postgres。

步骤:
    1. 在 Supabase 建好项目，拿到 Connection string (URI)
    2. export DATABASE_URL='postgresql://...'      (建议用 6543 的 pooler 地址)
    3. uv run python manage.py migrate             # 建 gloss_cache / auth 等 Django 管理的表
    4. uv run python scripts/sqlite_to_postgres.py # works / contents 由本脚本建表并导入

说明:
    works / contents 在 models.py 里是 managed=False，migrate 不会创建，所以这里手写建表 SQL。
    重复执行安全: 插入走 ON CONFLICT DO NOTHING。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

LEGACY_DDL = """
CREATE TABLE IF NOT EXISTS works (
    id bigserial PRIMARY KEY,
    author text,
    title text,
    urn text
);

CREATE TABLE IF NOT EXISTS contents (
    id bigserial PRIMARY KEY,
    work_id integer,
    text text,
    subtype text,
    div_type text,
    n_value text,
    path text,
    global_order integer
);

CREATE INDEX IF NOT EXISTS contents_work_id_path_idx ON contents (work_id, path);
CREATE INDEX IF NOT EXISTS contents_work_id_order_idx ON contents (work_id, global_order);
"""

TABLES = {
    "works": {
        "columns": ["id", "author", "title", "urn"],
        "conflict": "id",
    },
    "contents": {
        "columns": ["id", "work_id", "text", "subtype", "div_type", "n_value", "path", "global_order"],
        "conflict": "id",
    },
    "gloss_cache": {
        "columns": ["id", "text_hash", "gloss_data", "created_at"],
        "conflict": "text_hash",
    },
}

SEQUENCED_TABLES = ("works", "contents", "gloss_cache")


def _normalise(table: str, row: tuple) -> tuple:
    if table == "gloss_cache":
        row = list(row)
        if isinstance(row[2], str):
            try:
                row[2] = json.loads(row[2])
            except json.JSONDecodeError:
                pass
        # jsonb 列必须显式包装，psycopg 在 AUTO 模式下无法自适应 dict
        if isinstance(row[2], (dict, list)):
            row[2] = Jsonb(row[2])
        if isinstance(row[3], str):
            try:
                row[3] = datetime.fromisoformat(row[3])
            except ValueError:
                pass
        return tuple(row)
    return row


def copy_table(sqlite_conn: sqlite3.Connection, pg_conn: psycopg.Connection, table: str, batch_size: int) -> int:
    meta = TABLES[table]
    columns = meta["columns"]
    placeholders = ", ".join(["%s"] * len(columns))
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({meta['conflict']}) DO NOTHING"
    )

    cur = sqlite_conn.execute(f"SELECT {', '.join(columns)} FROM {table}")
    total = 0
    with pg_conn.cursor() as pg_cur:
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            pg_cur.executemany(sql, [_normalise(table, r) for r in rows])
            pg_conn.commit()
            total += len(rows)
            print(f"  {table}: {total} 行", flush=True)
    return total


def fix_sequences(pg_conn: psycopg.Connection) -> None:
    with pg_conn.cursor() as pg_cur:
        for table in SEQUENCED_TABLES:
            pg_cur.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE((SELECT MAX(id) FROM %s), 1))"
                % ("%s", table),
                (table,),
            )
    pg_conn.commit()


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="SQLite -> Postgres 导入")
    parser.add_argument("sqlite_path", nargs="?", default="latin_library.db")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument(
        "--tables",
        default=",".join(TABLES),
        help="只导入指定表，逗号分隔，默认全部",
    )
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("错误: 请先设置 DATABASE_URL", file=sys.stderr)
        return 1
    if not os.path.exists(args.sqlite_path):
        print(f"错误: 找不到 {args.sqlite_path}", file=sys.stderr)
        return 1

    sqlite_conn = sqlite3.connect(args.sqlite_path)
    with psycopg.connect(database_url) as pg_conn:
        print("建表 (works / contents)...")
        with pg_conn.cursor() as pg_cur:
            pg_cur.execute(LEGACY_DDL)
        pg_conn.commit()

        selected = [t.strip() for t in args.tables.split(",") if t.strip()]
        unknown = [t for t in selected if t not in TABLES]
        if unknown:
            print(f"错误: 未知表 {unknown}，可选: {', '.join(TABLES)}", file=sys.stderr)
            return 1

        for table in selected:
            print(f"导入 {table}...")
            total = copy_table(sqlite_conn, pg_conn, table, args.batch_size)
            print(f"{table} 完成: {total} 行")

        print("修正自增序列...")
        fix_sequences(pg_conn)

    sqlite_conn.close()
    print("全部完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
