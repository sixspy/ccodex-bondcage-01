"""增补扩笼：假绿事故复现 + 同笼扩额整笔落库 + 双人同补争用。

红测顺序（题面要求）：
1. test_topup_must_expand_cage_and_freeze_account —— 增补未扩额即见红；
2. test_two_writers_topup_same_cage_lock_contention —— 双人同补争用见红。
两条红测落地后才允许改 topup_svc / cages_svc / bonds_svc / reconcile_svc。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _seed_cage(c: TestClient, h: dict) -> dict:
    """种子冻结笼 CG-P-EAST-001：笼额 120000，账户冻结 120000。"""
    cages = c.get("/api/cages", headers=h).json()["items"]
    cage = next(x for x in cages if x["cage_code"] == "CG-P-EAST-001")
    assert cage["status"] == "已冻结"
    return cage


def _account(h_conn, party_key: str = "P-EAST") -> dict:
    row = h_conn.execute(
        "SELECT * FROM bond_accounts WHERE party_key=?", (party_key,)
    ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# 红测 1：增补必须真正把钱冻进笼里（复现“作业票有了、笼面与冻结不动”的假绿）
# ---------------------------------------------------------------------------
def test_topup_must_expand_cage_and_freeze_account():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed_cage(c, h)
    with connect() as conn:
        acct_before = _account(conn)
    assert acct_before["frozen"] == 120000
    assert acct_before["balance"] == 500000

    r = c.post(
        "/api/topup",
        headers=h,
        json={"cage_id": cage["id"], "amount": 30000, "mode": "同笼扩代"},
    )
    assert r.status_code == 200, r.text
    data = r.json()

    # 笼面：总额与可用同抬 30000，笼代推进
    after = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    assert after["amount"] == 150000
    assert after["available"] == 150000
    assert after["generation"] == cage["generation"] + 1 == 2
    assert data["generation"] == 2

    # 账户：冻结对齐 150000，余额不变（钱只从可用搬进冻结，未出系统）
    with connect() as conn:
        acct_after = _account(conn)
    assert acct_after["frozen"] == 150000
    assert acct_after["balance"] == 500000
    assert acct_after["balance"] - acct_after["frozen"] == 350000

    # 作业票状态不再是空桩
    jobs = c.get("/api/topup/jobs", headers=h).json()["items"]
    job = next(j for j in jobs if j["id"] == data["job_id"])
    assert job["status"] == "已扩笼"
    assert "空桩" not in (job["detail"] or "")

    # 对账互证必须是真绿：笼口径平衡 + 当事人冻结=活笼可用
    recon = c.get("/api/reconcile/CG-P-EAST-001", headers=h).json()
    assert recon["balanced"] is True
    assert recon["available"] == 150000
    assert recon["amount"] == 150000
    proof = c.get("/api/proof/P-EAST", headers=h).json()
    assert proof["frozen_matches_live"] is True
    assert proof["account"]["frozen"] == 150000
    assert proof["live_cage_available"] == 150000

    # 审计留痕
    audits = c.get("/api/audit?limit=200", headers=h).json()["items"]
    assert any(a["action"] == "topup_expand" and a["target"] == "CG-P-EAST-001" for a in audits)


def test_topup_rolls_back_when_any_step_fails(monkeypatch):
    """任一步失败：账户冻结与笼面数额一并回滚，作业票也不得残留。"""
    import topup_svc

    c = TestClient(app)
    h = _auth(c)
    cage = _seed_cage(c, h)

    def boom(*_a, **_kw):
        raise RuntimeError("模拟审计写入失败")

    monkeypatch.setattr(topup_svc, "write_audit", boom)
    r = c.post(
        "/api/topup",
        headers=h,
        json={"cage_id": cage["id"], "amount": 30000, "mode": "同笼扩代"},
    )
    assert r.status_code == 500
    monkeypatch.undo()

    after = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    assert after["amount"] == 120000
    assert after["available"] == 120000
    assert after["generation"] == 1
    with connect() as conn:
        acct = _account(conn)
        jobs = conn.execute("SELECT COUNT(*) c FROM topup_jobs").fetchone()["c"]
    assert acct["frozen"] == 120000
    assert jobs == 0


def test_topup_rejects_when_available_insufficient():
    c = TestClient(app)
    h = _auth(c)
    cage = _seed_cage(c, h)
    # P-EAST 可用 = 500000-120000 = 380000，补 400000 必拒，且零改
    r = c.post(
        "/api/topup",
        headers=h,
        json={"cage_id": cage["id"], "amount": 400000, "mode": "同笼扩代"},
    )
    assert r.status_code == 409
    after = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    assert after["amount"] == 120000
    with connect() as conn:
        acct = _account(conn)
    assert acct["frozen"] == 120000


def test_topup_deviation_from_minutes_is_refused_and_logged():
    """偏离纪要（乙路径/新笼桥接）：记扩笼偏航，拒绝任何写入。"""
    c = TestClient(app)
    h = _auth(c)
    cage = _seed_cage(c, h)
    r = c.post(
        "/api/topup",
        headers=h,
        json={"cage_id": cage["id"], "amount": 1000, "mode": "新笼桥接"},
    )
    assert r.status_code == 400
    with connect() as conn:
        jobs = conn.execute("SELECT COUNT(*) c FROM topup_jobs").fetchone()["c"]
        dev = conn.execute(
            "SELECT COUNT(*) c FROM audit_trail WHERE action='topup_deviation'"
        ).fetchone()["c"]
        acct = _account(conn)
    assert jobs == 0
    assert dev == 1
    assert acct["frozen"] == 120000


# ---------------------------------------------------------------------------
# 红测 2：两人同时补同一只笼须抢笼锁，后到只留争用痕
# ---------------------------------------------------------------------------
def test_two_writers_topup_same_cage_lock_contention():
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    cage = _seed_cage(c, h_admin)
    lock_key = f"cage:{cage['id']}"

    # 先到者持锁不放（模拟两人同时补同一只笼）
    assert (
        c.post(
            "/api/locks/acquire", headers=h_admin, json={"lock_key": lock_key}
        ).status_code
        == 200
    )

    # 后到者：409，只留争用痕，不写任何业务数据
    r = c.post(
        "/api/topup",
        headers=h_clerk,
        json={"cage_id": cage["id"], "amount": 30000, "mode": "同笼扩代"},
    )
    assert r.status_code == 409
    contention = c.get("/api/contention", headers=h_admin).json()["items"]
    hit = [x for x in contention if x["lock_key"] == lock_key and x["actor"] == "clerk"]
    assert len(hit) == 1
    with connect() as conn:
        jobs = conn.execute("SELECT COUNT(*) c FROM topup_jobs").fetchone()["c"]
        acct = _account(conn)
    assert jobs == 0
    assert acct["frozen"] == 120000

    # 锁释放后先到者补笼成功，数额只抬一笔
    assert (
        c.post(
            "/api/locks/release", headers=h_admin, json={"lock_key": lock_key}
        ).status_code
        == 200
    )
    ok = c.post(
        "/api/topup",
        headers=h_admin,
        json={"cage_id": cage["id"], "amount": 30000, "mode": "同笼扩代"},
    )
    assert ok.status_code == 200
    after = c.get(f"/api/cages/{cage['id']}", headers=h_admin).json()["cage"]
    assert after["amount"] == 150000
    assert after["available"] == 150000
    with connect() as conn:
        acct = _account(conn)
        jobs = conn.execute("SELECT COUNT(*) c FROM topup_jobs").fetchone()["c"]
    assert acct["frozen"] == 150000
    assert jobs == 1
