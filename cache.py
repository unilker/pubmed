# -*- coding: utf-8 -*-
"""
cache.py — sorgu → sayım kalıcı önbelleği (SQLite).

Anahtarsız çalışırken asıl kazanç hızı artırmak değil, istek sayısını
azaltmaktır. Aynı sorgu bir kez sorulur; TR ve EN kitapları peş peşe
işlendiğinde ya da bir koşu yarıda kaldığında ikinci kez ağa çıkılmaz.

Her kayıt zaman damgalıdır: PubMed büyüdüğü için bir sayım, tarihi
olmadan raporlanamaz. `max_age_days` ile eski kayıtlar geçersiz sayılır.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import sqlite3

_DDL = """
CREATE TABLE IF NOT EXISTS counts (
    backend    TEXT    NOT NULL,
    query      TEXT    NOT NULL,
    count      INTEGER,
    fetched_at TEXT    NOT NULL,
    PRIMARY KEY (backend, query)
)
"""


class CountCache:
    def __init__(self, path: str = "pubmed_cache.sqlite"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute(_DDL)
        self.conn.commit()

    # ------------------------------------------------------------- okuma/yazma
    def get(self, backend: str, query: str, max_age_days=None):
        """(sayı, zaman_damgası) veya None döndürür."""
        row = self.conn.execute(
            "SELECT count, fetched_at FROM counts WHERE backend=? AND query=?",
            (backend, query),
        ).fetchone()
        if not row:
            return None
        count, ts = row
        if max_age_days is not None:
            try:
                age = (_dt.datetime.now() - _dt.datetime.fromisoformat(ts)).days
            except ValueError:
                return None
            if age > max_age_days:
                return None
        return count, ts

    def put(self, backend: str, query: str, count, fetched_at=None) -> str:
        ts = fetched_at or _dt.datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO counts (backend, query, count, fetched_at) VALUES (?,?,?,?) "
            "ON CONFLICT(backend, query) DO UPDATE SET count=excluded.count, "
            "fetched_at=excluded.fetched_at",
            (backend, query, count, ts),
        )
        self.conn.commit()
        return ts

    # ------------------------------------------------------------------ bakım
    def stats(self):
        rows = self.conn.execute(
            "SELECT backend, COUNT(*), MIN(fetched_at), MAX(fetched_at) "
            "FROM counts GROUP BY backend ORDER BY backend"
        ).fetchall()
        return [{"backend": b, "kayıt": n, "en eski": lo, "en yeni": hi}
                for b, n, lo, hi in rows]

    def total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM counts").fetchone()[0]

    def clear(self, backend=None) -> int:
        cur = (self.conn.execute("DELETE FROM counts WHERE backend=?", (backend,))
               if backend else self.conn.execute("DELETE FROM counts"))
        self.conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------ taşınabilirlik
    def export_csv(self) -> bytes:
        sio = io.StringIO()
        w = csv.writer(sio)
        w.writerow(["backend", "query", "count", "fetched_at"])
        w.writerows(self.conn.execute(
            "SELECT backend, query, count, fetched_at FROM counts ORDER BY backend, query"
        ))
        return sio.getvalue().encode("utf-8-sig")

    def import_csv(self, data: bytes) -> int:
        text = data.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        n = 0
        for row in reader:
            if not row.get("backend") or not row.get("query"):
                continue
            try:
                cnt = int(row["count"]) if row.get("count") not in (None, "") else None
            except ValueError:
                cnt = None
            self.put(row["backend"], row["query"], cnt,
                     row.get("fetched_at") or None)
            n += 1
        return n
