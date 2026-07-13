"""SQLite persistence for the self-hosted SysVitals API."""

import os
import secrets
import sqlite3
import uuid
import json
import hashlib
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

            CREATE TABLE IF NOT EXISTS access_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                ts TEXT NOT NULL,
                cpu_name TEXT,
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
                memory_used_mb REAL,
                applications_open TEXT,
                uptime_seconds REAL,
                current_user TEXT,
                power_mode TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_telemetry_device_ts
                ON telemetry(device_id, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_devices_secret ON devices(device_secret);
            CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
            CREATE INDEX IF NOT EXISTS idx_access_tokens_user ON access_tokens(user_id);
            """
        )
        _add_telemetry_columns(connection)


def _add_telemetry_columns(connection: sqlite3.Connection) -> None:
    """Migrate telemetry databases created before supplemental vitals existed."""
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(telemetry)")
    }
    for column, sql_type in (
        ("cpu_name", "TEXT"),
        ("memory_used_mb", "REAL"),
        ("applications_open", "TEXT"),
        ("uptime_seconds", "REAL"),
        ("current_user", "TEXT"),
    ):
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE telemetry ADD COLUMN {column} {sql_type}")


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


def create_access_token(user_id: str, created_at: str) -> str:
    """Create a persistent bearer token without storing the raw secret."""
    token = f"svat_{secrets.token_urlsafe(32)}"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO access_tokens (token_hash, user_id, created_at) VALUES (?, ?, ?)",
            (token_hash, user_id, created_at),
        )
    return token


def get_user_by_access_token(token: str) -> dict[str, Any] | None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as connection:
        row = connection.execute(
            "SELECT user_id AS id FROM access_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
    return dict(row) if row else None


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
            "SELECT id, user_id, name, hostname FROM devices WHERE device_secret = ?",
            (device_secret,),
        ).fetchone()
    return dict(row) if row else None


def get_device(device_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, user_id, name, hostname FROM devices WHERE id = ?", (device_id,)
        ).fetchone()
    return dict(row) if row else None


def save_telemetry(device_id: str, timestamp: str, telemetry: dict[str, Any]) -> None:
    columns = (
        "cpu_name",
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
        "memory_used_mb",
        "applications_open",
        "uptime_seconds",
        "current_user",
        "power_mode",
    )
    values = [
        json.dumps(telemetry["applications_open"])
        if column == "applications_open" and telemetry.get(column) is not None
        else telemetry.get(column)
        for column in columns
    ]
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
    telemetry = dict(row) if row else None
    if telemetry and telemetry["applications_open"] is not None:
        try:
            telemetry["applications_open"] = json.loads(telemetry["applications_open"])
        except (TypeError, json.JSONDecodeError):
            telemetry["applications_open"] = []
    return telemetry


def get_telemetry_history(device_id: str, limit: int) -> list[dict[str, Any]]:
    """Return recent monitor payloads in chronological order for the JSON feed."""
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM (
                SELECT * FROM telemetry WHERE device_id = ? ORDER BY ts DESC LIMIT ?
            ) ORDER BY ts ASC
            """,
            (device_id, limit),
        ).fetchall()

    readings = [dict(row) for row in rows]
    for reading in readings:
        if reading["applications_open"] is not None:
            try:
                reading["applications_open"] = json.loads(reading["applications_open"])
            except (TypeError, json.JSONDecodeError):
                reading["applications_open"] = []
        for boolean_field in ("gpu_active", "ac_plugged"):
            if reading[boolean_field] is not None:
                reading[boolean_field] = bool(reading[boolean_field])
    return readings
