#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ledger SQLite — single source of truth para leads y toques."""

import sqlite3
from datetime import date, timedelta, datetime
from typing import List, Optional, Dict, Any

from sources.base import Lead


class Ledger:
    def __init__(self, db_path: str = "booking_ledger.db"):
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            promoter TEXT NOT NULL,
            event TEXT,
            city TEXT,
            country TEXT,
            event_date TEXT,
            url TEXT NOT NULL,
            contact_url TEXT,
            contact_email TEXT,
            genre_tags TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            first_seen TEXT NOT NULL,
            UNIQUE (source, external_id)
        )""")
        self._conn.execute("""CREATE TABLE IF NOT EXISTS touches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL REFERENCES leads(id),
            touched_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            note TEXT
        )""")
        self._conn.commit()

    def upsert_lead(self, lead: Lead) -> None:
        cur = self._conn.cursor()
        # Simple insert; if UNIQUE fails, update
        try:
            cur.execute("""INSERT INTO leads (source, external_id, promoter, event, city, country,
                event_date, url, contact_url, contact_email, genre_tags, status, first_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', date('now'))""",
                (lead.source, lead.external_id, lead.promoter, lead.event,
                 lead.city, lead.country, lead.event_date,
                 lead.url, lead.contact_url, lead.contact_email,
                 ",".join(lead.genre_tags)))
            self._conn.commit()
        except sqlite3.IntegrityError:
            cur.execute("""UPDATE leads SET promoter = promoter,
                event = event, city = city, country = country,
                event_date = event_date, url = url, contact_url = contact_url,
                contact_email = contact_email, genre_tags = genre_tags,
                status = 'new', first_seen = date('now')
                WHERE source = ? AND external_id = ?""",
                (lead.source, lead.external_id))
            self._conn.commit()

    def get_due(self, today_str: str) -> List[Dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("""SELECT l.id, l.promoter, l.event, l.city, l.country,
            l.contact_url, l.contact_email, l.genre_tags, l.status
            FROM leads l WHERE l.status IN ('new','qualified','contacted','replied')""")
        rows = cur.fetchall()
        due = []
        for row in rows:
            lead_id = row[0]
            cur.execute("SELECT COUNT(*) FROM touches WHERE lead_id = ?", (lead_id,))
            touch_count = cur.fetchone()[0]
            if touch_count >= 3:
                continue
            # Compute next touch
            if touch_count == 0:
                next_date = date.today()
            elif touch_count == 1:
                next_date = date.today() + timedelta(days=7)
            elif touch_count == 2:
                next_date = date.today() + timedelta(days=21)
            else:
                continue
            today_date = date.fromisoformat(today_str)
            if next_date <= today_date:
                due.append({
                    "id": row[0], "promoter": row[1], "event": row[2],
                    "city": row[3], "country": row[4],
                    "contact_url": row[5], "contact_email": row[6],
                    "genre_tags": row[7], "status": row[8]
                })
        return due

    def record_touch(self, lead_id: int, channel: str, note: Optional[str] = None) -> None:
        touched_at = date.today().isoformat() + " " + date.today().strftime("%H:%M:%S")
        self._conn.execute("INSERT INTO touches (lead_id, touched_at, channel, note) VALUES (?, ?, ?, ?)",
            (lead_id, touched_at, channel, note if note else ''))
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM touches WHERE lead_id = ?", (lead_id,))
        count = cur.fetchone()[0]
        if count == 3:
            self._conn.execute("UPDATE leads SET status = 'dead' WHERE id = ?", (lead_id,))
        self._conn.commit()

    def mark_replied(self, lead_id: int) -> None:
        self._conn.execute("UPDATE leads SET status = 'replied' WHERE id = ?", (lead_id,))
        self._conn.commit()

    def mark_booked(self, lead_id: int) -> None:
        self._conn.execute("UPDATE leads SET status = 'booked' WHERE id = ?", (lead_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
