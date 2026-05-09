import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / 'indexer_state.db'


def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    return conn


def get_last_commit() -> str | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            'SELECT value FROM state WHERE key = ?', ('last_commit',)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_last_commit(commit_hash: str) -> None:
    conn = _get_connection()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)',
            ('last_commit', commit_hash)
        )
        conn.commit()
    finally:
        conn.close()


def get_last_pr_id() -> int:
    conn = _get_connection()
    try:
        row = conn.execute(
            'SELECT value FROM state WHERE key = ?', ('last_pr_id',)
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def set_last_pr_id(pr_id: int) -> None:
    conn = _get_connection()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)',
            ('last_pr_id', str(pr_id))
        )
        conn.commit()
    finally:
        conn.close()
