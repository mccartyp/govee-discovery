from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Optional


def now_ms() -> int:
    return int(time.time() * 1000)


class RegistryStore:
    """
    SQLite-backed registry designed for:
      - raw event capture (scan_events)
      - latest per-device snapshot (devices)
      - query/response history (interrogations)
      - flexible key/value normalized attributes (device_kv)
      - optional device tagging (device_tags)

    The intent is to avoid schema migrations for most future feature expansion.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                ip TEXT,
                sku TEXT,
                ble_version_hard TEXT,
                ble_version_soft TEXT,
                wifi_version_hard TEXT,
                wifi_version_soft TEXT,
                mac TEXT,
                last_scan_payload TEXT,
                last_status_payload TEXT,
                first_seen_ms INTEGER NOT NULL,
                last_seen_ms INTEGER NOT NULL,
                extra_json TEXT
            );

            CREATE TABLE IF NOT EXISTS scan_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at_ms INTEGER NOT NULL,
                src_ip TEXT NOT NULL,
                src_port INTEGER NOT NULL,
                payload_raw TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS interrogations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT,
                ip TEXT,
                cmd TEXT NOT NULL,
                sent_at_ms INTEGER NOT NULL,
                received_at_ms INTEGER,
                success INTEGER NOT NULL,
                error TEXT,
                request_json TEXT NOT NULL,
                response_json TEXT,
                FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );

            CREATE TABLE IF NOT EXISTS device_kv (
                device_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY(device_id, key),
                FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );

            CREATE TABLE IF NOT EXISTS device_tags (
                device_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY(device_id, tag),
                FOREIGN KEY(device_id) REFERENCES devices(device_id)
            );

            CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen_ms);
            CREATE INDEX IF NOT EXISTS idx_scan_events_received ON scan_events(received_at_ms);
            CREATE INDEX IF NOT EXISTS idx_interrogations_sent ON interrogations(sent_at_ms);
            """
        )
        self.conn.commit()

    # ---------- Persistence primitives ----------

    def insert_scan_event(self, received_at_ms: int, src_ip: str, src_port: int, payload_raw: str) -> None:
        self.conn.execute(
            "INSERT INTO scan_events(received_at_ms, src_ip, src_port, payload_raw) VALUES(?,?,?,?)",
            (received_at_ms, src_ip, src_port, payload_raw),
        )
        self.conn.commit()

    def upsert_device_from_scan(
        self,
        device_id: str,
        ip: Optional[str],
        sku: Optional[str],
        ble_hw: Optional[str],
        ble_sw: Optional[str],
        wifi_hw: Optional[str],
        wifi_sw: Optional[str],
        mac: Optional[str],
        scan_payload_raw: str,
        seen_ms: int,
    ) -> None:
        cur = self.conn.execute("SELECT first_seen_ms FROM devices WHERE device_id=?", (device_id,))
        row = cur.fetchone()
        first_seen_ms = row[0] if row else seen_ms

        self.conn.execute(
            """
            INSERT INTO devices(
                device_id, ip, sku,
                ble_version_hard, ble_version_soft,
                wifi_version_hard, wifi_version_soft,
                mac,
                last_scan_payload,
                first_seen_ms, last_seen_ms,
                extra_json
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET
                ip=COALESCE(excluded.ip, devices.ip),
                sku=COALESCE(excluded.sku, devices.sku),
                ble_version_hard=COALESCE(excluded.ble_version_hard, devices.ble_version_hard),
                ble_version_soft=COALESCE(excluded.ble_version_soft, devices.ble_version_soft),
                wifi_version_hard=COALESCE(excluded.wifi_version_hard, devices.wifi_version_hard),
                wifi_version_soft=COALESCE(excluded.wifi_version_soft, devices.wifi_version_soft),
                mac=COALESCE(excluded.mac, devices.mac),
                last_scan_payload=excluded.last_scan_payload,
                last_seen_ms=excluded.last_seen_ms
            """,
            (
                device_id,
                ip,
                sku,
                ble_hw,
                ble_sw,
                wifi_hw,
                wifi_sw,
                mac,
                scan_payload_raw,
                first_seen_ms,
                seen_ms,
                json.dumps({}, separators=(",", ":")),
            ),
        )
        self.conn.commit()

    def record_interrogation(
        self,
        device_id: Optional[str],
        ip: str,
        cmd: str,
        sent_at_ms: int,
        received_at_ms: Optional[int],
        success: bool,
        error: Optional[str],
        request_obj: dict[str, Any],
        response_obj: Optional[dict[str, Any]],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO interrogations(
                device_id, ip, cmd,
                sent_at_ms, received_at_ms,
                success, error,
                request_json, response_json
            )
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                device_id,
                ip,
                cmd,
                sent_at_ms,
                received_at_ms,
                1 if success else 0,
                error,
                json.dumps(request_obj, ensure_ascii=False, separators=(",", ":")),
                json.dumps(response_obj, ensure_ascii=False, separators=(",", ":")) if response_obj is not None else None,
            ),
        )

        if success and device_id and response_obj is not None and cmd == "devStatus":
            self.conn.execute(
                "UPDATE devices SET last_status_payload=? WHERE device_id=?",
                (json.dumps(response_obj, ensure_ascii=False, separators=(",", ":")), device_id),
            )

        self.conn.commit()

    def set_kv(self, device_id: str, key: str, value: Any) -> None:
        self.conn.execute(
            """
            INSERT INTO device_kv(device_id, key, value, updated_at_ms)
            VALUES(?,?,?,?)
            ON CONFLICT(device_id, key) DO UPDATE SET
                value=excluded.value,
                updated_at_ms=excluded.updated_at_ms
            """,
            (device_id, key, json.dumps(value, ensure_ascii=False), now_ms()),
        )
        self.conn.commit()

    # ---------- Query helpers for JSON dump ----------

    def dump_devices(self) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            """
            SELECT device_id, ip, sku, ble_version_hard, ble_version_soft,
                   wifi_version_hard, wifi_version_soft, mac,
                   first_seen_ms, last_seen_ms,
                   last_scan_payload, last_status_payload, extra_json
            FROM devices
            ORDER BY last_seen_ms DESC
            """
        )
        rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "device_id": r[0],
                    "ip": r[1],
                    "sku": r[2],
                    "ble_version_hard": r[3],
                    "ble_version_soft": r[4],
                    "wifi_version_hard": r[5],
                    "wifi_version_soft": r[6],
                    "mac": r[7],
                    "first_seen_ms": r[8],
                    "last_seen_ms": r[9],
                    "last_scan_payload": json.loads(r[10]) if r[10] else None,
                    "last_status_payload": json.loads(r[11]) if r[11] else None,
                    "extra": json.loads(r[12]) if r[12] else {},
                }
            )
        return out

    def dump_scan_events(self, limit: int = 2000, since_ms: Optional[int] = None) -> list[dict[str, Any]]:
        if since_ms is None:
            cur = self.conn.execute(
                """
                SELECT received_at_ms, src_ip, src_port, payload_raw
                FROM scan_events
                ORDER BY received_at_ms DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            cur = self.conn.execute(
                """
                SELECT received_at_ms, src_ip, src_port, payload_raw
                FROM scan_events
                WHERE received_at_ms >= ?
                ORDER BY received_at_ms DESC
                LIMIT ?
                """,
                (since_ms, limit),
            )
        rows = cur.fetchall()
        return [
            {"received_at_ms": r[0], "src": {"ip": r[1], "port": r[2]}, "payload_raw": r[3]}
            for r in rows
        ]

    def dump_interrogations(self, limit: int = 2000, since_ms: Optional[int] = None) -> list[dict[str, Any]]:
        if since_ms is None:
            cur = self.conn.execute(
                """
                SELECT device_id, ip, cmd, sent_at_ms, received_at_ms, success, error, request_json, response_json
                FROM interrogations
                ORDER BY sent_at_ms DESC
                LIMIT ?
                """,
                (limit,),
            )
        else:
            cur = self.conn.execute(
                """
                SELECT device_id, ip, cmd, sent_at_ms, received_at_ms, success, error, request_json, response_json
                FROM interrogations
                WHERE sent_at_ms >= ?
                ORDER BY sent_at_ms DESC
                LIMIT ?
                """,
                (since_ms, limit),
            )
        rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "device_id": r[0],
                    "ip": r[1],
                    "cmd": r[2],
                    "sent_at_ms": r[3],
                    "received_at_ms": r[4],
                    "success": bool(r[5]),
                    "error": r[6],
                    "request": json.loads(r[7]) if r[7] else None,
                    "response": json.loads(r[8]) if r[8] else None,
                }
            )
        return out

    def dump_kv(
        self,
        device_id: Optional[str] = None,
        key_prefix: Optional[str] = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []

        if device_id:
            where.append("device_id = ?")
            params.append(device_id)

        if key_prefix:
            where.append("key LIKE ?")
            params.append(f"{key_prefix}%")

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        cur = self.conn.execute(
            f"""
            SELECT device_id, key, value, updated_at_ms
            FROM device_kv
            {where_sql}
            ORDER BY updated_at_ms DESC
            LIMIT ?
            """,
            (*params, limit),
        )

        rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "device_id": r[0],
                    "key": r[1],
                    "value": json.loads(r[2]) if r[2] else None,
                    "updated_at_ms": r[3],
                }
            )
        return out

    def list_device_targets(self) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT device_id, ip, sku FROM devices WHERE ip IS NOT NULL ORDER BY last_seen_ms DESC"
        )
        return [{"device_id": r[0], "ip": r[1], "sku": r[2]} for r in cur.fetchall()]

    def get_device_ip(self, device_id: str) -> Optional[str]:
        cur = self.conn.execute("SELECT ip FROM devices WHERE device_id=?", (device_id,))
        row = cur.fetchone()
        if not row:
            return None
        return row[0]
