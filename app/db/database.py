import os
from contextlib import contextmanager

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor


load_dotenv("/opt/bound/.env")


@contextmanager
def get_db():
    conn = psycopg2.connect(
        os.environ["DATABASE_URL"]
    )

    try:
        yield conn

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def fetch_one(query: str, params=None):
    with get_db() as conn:
        try:
            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:
                cur.execute(query, params)
                row = cur.fetchone()

            conn.commit()

            return row

        except Exception:
            conn.rollback()
            raise


def fetch_all(query: str, params=None):
    with get_db() as conn:
        try:
            with conn.cursor(
                cursor_factory=RealDictCursor
            ) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

            conn.commit()

            return rows

        except Exception:
            conn.rollback()
            raise


def execute(query: str, params=None):
    with get_db() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)

            conn.commit()

        except Exception:
            conn.rollback()
            raise
