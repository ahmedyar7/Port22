import sqlite3
from datetime import datetime


def init_db(path="chat.db"):
    conn = sqlite3.connect(path)
    conn.execute("""

    CREATE TABLE IF NOT EXISTS messages(
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        peer          TEXT NOT NULL,
        direction     TEXT NOT NULL,
        body          TEXT NOT NULL,
        ts            TEXT NOT NULL
)
""")
    conn.commit()
    return conn


def save_messages(conn, peer, direction, body):
    conn.execute(
        "INSERT INTO messages (peer, direction, body, ts) VALUES (?, ?, ?, ?)",
        (peer, direction, body.strip(), datetime.now().isoformat()),
    )
    conn.commit()


def load_history(conn, peer, limit=50):
    rows = conn.execute(
        "SELECT direction, body, ts FROM messages WHERE peer=? ORDER BY id DESC LIMIT ?",
        (peer, limit),
    ).fetchall()

    return list(reversed(rows))  # oldest first
