"""HOSHIYOMI LOG — ローカル保存 (SQLite)

データはすべて ~/.hoshiyomi/hoshiyomi.db に置く。外部送信なし。
（LLM はローカルの llama-server にのみ問い合わせる）
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
from pathlib import Path

DATA_DIR = Path.home() / ".hoshiyomi"
DB_PATH = DATA_DIR / "hoshiyomi.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS readings (
    date TEXT PRIMARY KEY,          -- YYYY-MM-DD
    facts TEXT,                      -- LLM に渡した天体データ
    personal TEXT,                   -- ホロスコープ由来の星読み
    general TEXT,                    -- 貼り付けた一般の星座占い
    updated TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS journal (
    date TEXT PRIMARY KEY,
    text TEXT,
    score INTEGER,                   -- 1-5 その日の手応え
    updated TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS mundane (
    anchor TEXT,                     -- 期間の初日 YYYY-MM-DD（週=月曜 / 月=1日 / 年=1月1日）
    period TEXT,                     -- week | month | year
    facts TEXT,                      -- LLM に渡した天体データ
    text TEXT,                       -- 社会の星読み
    model TEXT,                      -- 書いたモデル
    review TEXT,                     -- 期間が過ぎてから書く「実際どうだったか」
    score INTEGER,                   -- 当たり度 1-5（未採点は NULL）
    updated TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (anchor, period)
);
CREATE TABLE IF NOT EXISTS trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,                       -- 正解の日付（日記の日）
    kind TEXT,                       -- personal | general
    option_dates TEXT,               -- JSON: 選択肢の日付順
    answer_idx INTEGER,
    picked_idx INTEGER,
    created TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

DEFAULTS = {
    "llm_url": "http://localhost:8000/v1",
    "llm_model": "local",
    "llm_auto": "1",  # 1 = ロード中の llama-server に自動で合わせる / 0 = 下の URL・Model を使う
    "utc_offset": "9.0",
}


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---- settings ----
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is not None:
            return row["value"]
        return DEFAULTS.get(key, default)

    def set_many(self, items: dict):
        self.db.executemany(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(k, None if v is None else str(v)) for k, v in items.items()],
        )
        self.db.commit()

    # ---- readings ----
    def reading(self, date: str):
        return self.db.execute("SELECT * FROM readings WHERE date=?", (date,)).fetchone()

    def save_reading(self, date: str, facts: str, personal: str, general: str):
        self.db.execute(
            """INSERT INTO readings(date,facts,personal,general,updated) VALUES(?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(date) DO UPDATE SET facts=excluded.facts, personal=excluded.personal,
               general=excluded.general, updated=CURRENT_TIMESTAMP""",
            (date, facts, personal, general),
        )
        self.db.commit()

    # ---- 社会の星読み ----
    def mundane(self, anchor: str, period: str):
        return self.db.execute("SELECT * FROM mundane WHERE anchor=? AND period=?", (anchor, period)).fetchone()

    def save_mundane(self, anchor: str, period: str, facts: str, text: str, model: str, review: str, score):
        self.db.execute(
            """INSERT INTO mundane(anchor,period,facts,text,model,review,score,updated)
               VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(anchor,period) DO UPDATE SET facts=excluded.facts, text=excluded.text,
               model=excluded.model, review=excluded.review, score=excluded.score, updated=CURRENT_TIMESTAMP""",
            (anchor, period, facts, text, model, review, score),
        )
        self.db.commit()

    def mundane_scores(self):
        """期間ごとの当たり度の件数と平均"""
        return self.db.execute(
            "SELECT period, COUNT(score) AS n, AVG(score) AS avg FROM mundane GROUP BY period"
        ).fetchall()

    # ---- journal ----
    def journal(self, date: str):
        return self.db.execute("SELECT * FROM journal WHERE date=?", (date,)).fetchone()

    def save_journal(self, date: str, text: str, score: int):
        self.db.execute(
            """INSERT INTO journal(date,text,score,updated) VALUES(?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(date) DO UPDATE SET text=excluded.text, score=excluded.score, updated=CURRENT_TIMESTAMP""",
            (date, text, score),
        )
        self.db.commit()

    def recent_journal(self, n: int = 30):
        return self.db.execute("SELECT * FROM journal ORDER BY date DESC LIMIT ?", (n,)).fetchall()

    # ---- blind evaluation ----
    def _pool(self, kind: str) -> dict[str, str]:
        """日記と、その日の占い文(kind)が両方ある日付 → 占い文"""
        col = "personal" if kind == "personal" else "general"
        rows = self.db.execute(
            f"""SELECT r.date, r.{col} AS txt FROM readings r JOIN journal j ON j.date=r.date
                WHERE TRIM(COALESCE(r.{col},''))<>'' AND TRIM(COALESCE(j.text,''))<>''"""
        ).fetchall()
        return {r["date"]: r["txt"] for r in rows}

    def pool_sizes(self) -> dict[str, int]:
        return {k: len(self._pool(k)) for k in ("personal", "general")}

    def make_trial(self, kind: str, n_options: int = 4):
        """正解1 + おとり(n-1) をシャッフル。未出題の日を優先。"""
        pool = self._pool(kind)
        if len(pool) < n_options:
            return None
        done = {
            r["date"]
            for r in self.db.execute("SELECT date FROM trials WHERE kind=? AND picked_idx IS NOT NULL", (kind,))
        }
        fresh = [d for d in pool if d not in done] or list(pool)
        target = random.choice(fresh)
        decoys = random.sample([d for d in pool if d != target], n_options - 1)
        opts = decoys + [target]
        random.shuffle(opts)
        cur = self.db.execute(
            "INSERT INTO trials(date,kind,option_dates,answer_idx) VALUES(?,?,?,?)",
            (target, kind, json.dumps(opts), opts.index(target)),
        )
        self.db.commit()
        return {
            "id": cur.lastrowid,
            "kind": kind,
            "journal": self.journal(target)["text"],
            "options": [pool[d] for d in opts],
            "option_dates": opts,
            "answer_idx": opts.index(target),
        }

    def answer_trial(self, trial_id: int, picked: int):
        self.db.execute("UPDATE trials SET picked_idx=? WHERE id=?", (picked, trial_id))
        self.db.commit()

    def stats(self, n_options: int = 4) -> dict[str, dict]:
        out = {}
        for kind in ("personal", "general"):
            rows = self.db.execute(
                "SELECT answer_idx, picked_idx FROM trials WHERE kind=? AND picked_idx IS NOT NULL", (kind,)
            ).fetchall()
            n = len(rows)
            hits = sum(1 for r in rows if r["answer_idx"] == r["picked_idx"])
            out[kind] = {
                "n": n,
                "hits": hits,
                "rate": hits / n if n else 0.0,
                "chance": 1 / n_options,
                "p": binom_sf(hits, n, 1 / n_options) if n else 1.0,
            }
        return out


def binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) 片側。偶然でこれ以上当たる確率。"""
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))
