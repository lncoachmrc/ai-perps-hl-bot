from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extensions import connection as PGConnection

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logger = logging.getLogger(__name__)


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SQL_PATH = ROOT_DIR / "migrations" / "001_init.sql"


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing. Cannot initialize database schema.")
    return database_url


def read_sql_file(sql_path: Path) -> str:
    if not sql_path.exists():
        raise FileNotFoundError(f"SQL migration file not found: {sql_path}")
    return sql_path.read_text(encoding="utf-8")


def connect(database_url: str) -> PGConnection:
    logger.info("Connecting to PostgreSQL")
    return psycopg2.connect(database_url)


def run_schema_bootstrap(conn: PGConnection, sql: str) -> None:
    with conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    logger.info("Database schema bootstrap completed")


def main() -> None:
    database_url = get_database_url()
    sql_path = Path(os.getenv("DB_INIT_SQL_PATH", str(DEFAULT_SQL_PATH))).resolve()
    sql = read_sql_file(sql_path)

    conn = connect(database_url)
    try:
        run_schema_bootstrap(conn, sql)
    finally:
        conn.close()
        logger.info("PostgreSQL connection closed")


if __name__ == "__main__":
    main()
