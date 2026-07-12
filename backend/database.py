"""SQLite persistence for the self-hosted SysVitals API."""

import os
import secrets
import sqlite3
import uuid
from pathlib import Path
from typing import Any


DATABASE_PATH = Path(
    os.environ.get("DATABASE_PATH", Path(__file__).parent / "data" / "sysvitals.db")
)


def _connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    """Create the application schema when the database is first mounted."""
    with _connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                hostname TEXT,
                device_secret TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                last_seen TEXT
            );

            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                ts TEXT NOT NULL,
                cpu_temp REAL,
                cpu_power REAL,
                cpu_clock REAL,
                cpu_util REAL,
                gpu_name TEXT,
                gpu_temp REAL,
                gpu_power REAL,
                gpu_util REAL,
                gpu_mem_used REAL,
                gpu_mem_total REAL,
                gpu_active INTEGER,
                ac_plugged INTEGER,
                battery_power REAL,
                battery_voltage REAL,
                battery_level REAL,
                power_mode TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_telemetry_device_ts
                ON telemetry(device_id, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_devices_secret ON devices(device_secret);
            CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
            """
        )


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


def create_user(username: str, password_hash: str, created_at: str) -> str | None:
    user_id = str(uuid.uuid4())
    try:
        with _connect() as connection:
            connection.execute(
                "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (user_id, username, password_hash, created_at),
            )
    except sqlite3.IntegrityError:
        return None
    return user_id


def create_device(
    user_id: str, name: str, hostname: str | None, created_at: str
) -> str | None:
    device_id = str(uuid.uuid4())
    device_secret = f"sv_{secrets.token_hex(24)}"
    try:
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO devices (id, user_id, name, hostname, device_secret, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (device_id, user_id, name, hostname, device_secret, created_at),
            )
    except sqlite3.IntegrityError:
        return None
    return device_secret


def get_user_devices(user_id: str) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, name, hostname, created_at, last_seen
            FROM devices WHERE user_id = ? ORDER BY created_at
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_device_by_secret(device_secret: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, name, hostname FROM devices WHERE device_secret = ?",
            (device_secret,),
        ).fetchone()
    return dict(row) if row else None


def save_telemetry(device_id: str, timestamp: str, telemetry: dict[str, Any]) -> None:
    columns = (
        "cpu_temp",
        "cpu_power",
        "cpu_clock",
        "cpu_util",
        "gpu_name",
        "gpu_temp",
        "gpu_power",
        "gpu_util",
        "gpu_mem_used",
        "gpu_mem_total",
        "gpu_active",
        "ac_plugged",
        "battery_power",
        "battery_voltage",
        "battery_level",
        "power_mode",
    )
    values = [telemetry.get(column) for column in columns]
    with _connect() as connection:
        connection.execute(
            "UPDATE devices SET last_seen = ? WHERE id = ?", (timestamp, device_id)
        )
        connection.execute(
            f"INSERT INTO telemetry (device_id, ts, {', '.join(columns)}) "
            f"VALUES (?, ?, {', '.join('?' for _ in columns)})",
            (device_id, timestamp, *values),
        )


def get_latest_telemetry(device_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM telemetry WHERE device_id = ? ORDER BY ts DESC LIMIT 1",
            (device_id,),
        ).fetchone()
    return dict(row) if row else None
