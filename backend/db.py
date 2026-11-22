"""SQLite：保证金笼母仓公共库层。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB_PATH = DATA / "app.db"
FIXTURES = ROOT / "fixtures"
FRONTEND = ROOT / "frontend"

PRODUCT_META = {
    "product": "保证金笼",
    "skeleton": "保证金先入笼冻结，履约核验通过才解笼拨付，违约进罚没册，笼代与拨付罚没对账",
    "modules": [
        "bonds",
        "cages",
        "attest",
        "release",
        "forfeit",
        "reconcile",
        "locks",
        "audit",
        "topup",
        "split",
    ],
    # 增补已按甲路径落地；拆笼仍为空桩。
    "stubs": ["split_cage"],
    "topup_path": "甲",
}

# 仍占用当事人冻结额、可继续核验/解笼/罚没的活笼状态。
LIVE_CAGE_STATUSES = ("已冻结", "核验通过", "核验违约", "部分解笼", "部分罚没")
# 乙路径封存后只读历史态；不得再作解笼/增补目标。
SEALED_CAGE_STATUSES = ("已封存",)

# 增补扩笼命运说明：当日唯一口径，锁死后不接受偏离。
TOPUP_FATE_PATH = "甲"
TOPUP_FATE_MODE = "同笼扩代"
TOPUP_FATE_NOTE = (
    "今日增补扩笼唯一口径：甲路径「同笼扩代就地扩额」——同一提交整笔改写原笼行"
    "总额与可用、抬账户冻结、升代并记审计，笼主键与对外笼号保持不变，任一步失败"
    "账户冻结与笼面整笔回滚。乙路径（封存旧笼再开新笼）今日不采用；偏离即记扩笼"
    "偏航并拒绝写入。禁止第三路。"
)


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def sha_password(password: str) -> str:
    import hashlib

    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  username TEXT PRIMARY KEY,
  password_sha TEXT NOT NULL,
  role TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bond_accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  party_key TEXT UNIQUE NOT NULL,
  party_name TEXT NOT NULL,
  balance REAL NOT NULL DEFAULT 0,
  frozen REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cage_code TEXT UNIQUE NOT NULL,
  party_key TEXT NOT NULL,
  contract_no TEXT NOT NULL,
  amount REAL NOT NULL,
  available REAL NOT NULL,
  generation INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,
  beneficiary TEXT NOT NULL,
  created_at TEXT NOT NULL,
  frozen_at TEXT,
  FOREIGN KEY(party_key) REFERENCES bond_accounts(party_key)
);
CREATE TABLE IF NOT EXISTS attestations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cage_id INTEGER NOT NULL,
  outcome TEXT NOT NULL,
  evidence TEXT NOT NULL,
  attest_gen INTEGER NOT NULL,
  officer TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(cage_id) REFERENCES cages(id)
);
CREATE TABLE IF NOT EXISTS releases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cage_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  payee TEXT NOT NULL,
  cage_generation INTEGER NOT NULL,
  status TEXT NOT NULL,
  release_ref TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(cage_id) REFERENCES cages(id)
);
CREATE TABLE IF NOT EXISTS forfeits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cage_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  reason TEXT NOT NULL,
  cage_generation INTEGER NOT NULL,
  status TEXT NOT NULL,
  forfeit_ref TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(cage_id) REFERENCES cages(id)
);
CREATE TABLE IF NOT EXISTS cage_locks(
  lock_key TEXT PRIMARY KEY,
  holder TEXT NOT NULL,
  acquired_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contention_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lock_key TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_trail(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  payload TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topup_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cage_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL,
  generation_before INTEGER,
  generation_after INTEGER,
  formal_cage_id INTEGER,
  formal_cage_code TEXT,
  topup_ref TEXT,
  fate_path TEXT
);
CREATE TABLE IF NOT EXISTS topup_fate(
  fate_date TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  mode TEXT NOT NULL,
  note TEXT NOT NULL,
  locked INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS split_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cage_id INTEGER NOT NULL,
  left_amount REAL NOT NULL,
  right_amount REAL NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deposit_ledger(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  party_key TEXT NOT NULL,
  amount REAL NOT NULL,
  memo TEXT NOT NULL,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL
);
"""


def write_audit(conn: sqlite3.Connection, actor: str, action: str, target: str, payload: dict) -> None:
    conn.execute(
        """INSERT INTO audit_trail(actor,action,target,payload,created_at)
           VALUES (?,?,?,?,?)""",
        (actor, action, target, json.dumps(payload, ensure_ascii=False), now_iso()),
    )


def today_str() -> str:
    from datetime import date

    return date.today().isoformat()


def ensure_fate(conn: sqlite3.Connection) -> dict:
    """当日增补扩笼命运说明：首次写入即锁死，书目唯一，不接受改写。"""
    day = today_str()
    row = conn.execute(
        "SELECT * FROM topup_fate WHERE fate_date=?", (day,)
    ).fetchone()
    if row:
        return dict(row)
    conn.execute(
        """INSERT INTO topup_fate(fate_date,path,mode,note,locked,created_at)
           VALUES (?,?,?,?,?,?)""",
        (day, TOPUP_FATE_PATH, TOPUP_FATE_MODE, TOPUP_FATE_NOTE, 1, now_iso()),
    )
    return dict(
        conn.execute(
            "SELECT * FROM topup_fate WHERE fate_date=?", (day,)
        ).fetchone()
    )


def party_live_available(conn: sqlite3.Connection, party_key: str) -> float:
    """当事人活笼可用合计（不含已封存旧笼），与账户冻结同源比对。"""
    marks = ",".join("?" for _ in LIVE_CAGE_STATUSES)
    return float(
        conn.execute(
            f"""SELECT COALESCE(SUM(available),0) s FROM cages
                WHERE party_key=? AND status IN ({marks})""",
            (party_key, *LIVE_CAGE_STATUSES),
        ).fetchone()["s"]
    )


def formal_cage_for_party(conn: sqlite3.Connection, party_key: str) -> dict | None:
    """当事人当前正式可解笼：不按笼号，只按活笼状态取最近一只。

    甲路径下即原笼主键；乙路径若启用则为新开的正式笼行。封存旧笼不在其列。
    """
    marks = ",".join("?" for _ in LIVE_CAGE_STATUSES)
    row = conn.execute(
        f"""SELECT * FROM cages
            WHERE party_key=? AND status IN ({marks})
            ORDER BY id DESC LIMIT 1""",
        (party_key, *LIVE_CAGE_STATUSES),
    ).fetchone()
    return dict(row) if row else None


def migrate(conn: sqlite3.Connection) -> None:
    """为既有库补列/补表；库文件路径不变。"""
    cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(topup_jobs)").fetchall()
    }
    for name, decl in (
        ("generation_before", "INTEGER"),
        ("generation_after", "INTEGER"),
        ("formal_cage_id", "INTEGER"),
        ("formal_cage_code", "TEXT"),
        ("topup_ref", "TEXT"),
        ("fate_path", "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE topup_jobs ADD COLUMN {name} {decl}")


def _load_json(name: str):
    path = FIXTURES / name
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def seed_users(conn: sqlite3.Connection) -> None:
    for u, role in (("admin", "admin"), ("clerk", "clerk"), ("viewer", "viewer")):
        conn.execute(
            "INSERT OR IGNORE INTO users(username,password_sha,role) VALUES (?,?,?)",
            (u, sha_password("123456"), role),
        )


def seed_bonds(conn: sqlite3.Connection) -> None:
    for row in _load_json("bonds.json"):
        exists = conn.execute(
            "SELECT id FROM bond_accounts WHERE party_key=?", (row["party_key"],)
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """INSERT INTO bond_accounts(party_key,party_name,balance,frozen,status,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                row["party_key"],
                row["party_name"],
                float(row["balance"]),
                float(row.get("frozen", 0)),
                row.get("status", "正常"),
                row.get("created_at", now_iso()),
            ),
        )
        if float(row["balance"]) > 0:
            conn.execute(
                """INSERT INTO deposit_ledger(party_key,amount,memo,created_at,actor)
                   VALUES (?,?,?,?,?)""",
                (
                    row["party_key"],
                    float(row["balance"]),
                    "种子入金",
                    row.get("created_at", now_iso()),
                    "seed",
                ),
            )


def seed_cages(conn: sqlite3.Connection) -> None:
    for row in _load_json("cages.json"):
        exists = conn.execute(
            "SELECT id FROM cages WHERE cage_code=?", (row["cage_code"],)
        ).fetchone()
        if exists:
            continue
        amount = float(row["amount"])
        status = row.get("status", "已冻结")
        available = float(row.get("available", amount if status == "已冻结" else 0))
        conn.execute(
            """INSERT INTO cages(cage_code,party_key,contract_no,amount,available,
               generation,status,beneficiary,created_at,frozen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                row["cage_code"],
                row["party_key"],
                row["contract_no"],
                amount,
                available,
                int(row.get("generation", 1)),
                status,
                row["beneficiary"],
                row.get("created_at", now_iso()),
                row.get("frozen_at"),
            ),
        )
        if status == "已冻结":
            acct = conn.execute(
                "SELECT id,frozen FROM bond_accounts WHERE party_key=?",
                (row["party_key"],),
            ).fetchone()
            if acct:
                # 种子笼已反映在 balance/frozen 字段时不重复扣
                pass


def seed_attest_samples(conn: sqlite3.Connection) -> None:
    """为已冻结笼预置一条待核验说明，便于页面冷启动有可读业务。"""
    rows = conn.execute(
        "SELECT id, generation FROM cages WHERE status='已冻结' ORDER BY id"
    ).fetchall()
    for cage in rows[:2]:
        n = conn.execute(
            "SELECT COUNT(*) c FROM attestations WHERE cage_id=?", (cage["id"],)
        ).fetchone()["c"]
        if n:
            continue
        # 不预置通过/违约结论，只保证表可查；实际核验由业务动作写入


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)
        seed_users(conn)
        seed_bonds(conn)
        seed_cages(conn)
        seed_attest_samples(conn)
        ensure_fate(conn)
        conn.commit()


def dashboard_stats(conn: sqlite3.Connection) -> dict:
    bonds = conn.execute("SELECT COUNT(*) c FROM bond_accounts").fetchone()["c"]
    cages = conn.execute("SELECT COUNT(*) c FROM cages").fetchone()["c"]
    frozen = conn.execute(
        "SELECT COALESCE(SUM(available),0) s FROM cages WHERE status='已冻结'"
    ).fetchone()["s"]
    released = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM releases WHERE status='已拨付'"
    ).fetchone()["s"]
    forfeited = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM forfeits WHERE status='已罚没'"
    ).fetchone()["s"]
    stubs = {
        "topup_jobs": conn.execute("SELECT COUNT(*) c FROM topup_jobs").fetchone()["c"],
        "split_jobs": conn.execute("SELECT COUNT(*) c FROM split_jobs").fetchone()["c"],
    }
    return {
        "bonds": bonds,
        "cages": cages,
        "frozen_available": frozen,
        "released_total": released,
        "forfeited_total": forfeited,
        "stubs": stubs,
        "meta": PRODUCT_META,
    }
