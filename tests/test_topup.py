"""增补扩额（甲路径）回归夹具：先见红，再实现。

事故夹具：种子冻结笼点增补后，作业票出现，而笼额/可用与账户冻结不动，
互证灯仍绿——这是假绿。以下断言以「同笼主键就地扩额」为唯一正确口径。
"""
import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db


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


def _cage(c: TestClient, h: dict, code: str = "CG-P-EAST-001") -> dict:
    rows = c.get("/api/cages", headers=h).json()["items"]
    return [x for x in rows if x["cage_code"] == code][0]


def test_topup_must_expand_seed_cage_account_and_keep_proof_truthful():
    """增补未扩额的假绿事故：笼额、可用、账户冻结必须同笔上升。"""
    c = TestClient(app)
    h = _auth(c)
    cage = _cage(c, h)
    before_acct = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    before_proof = c.get("/api/proof/P-EAST", headers=h).json()
    # 种子里账户冻结与活笼可用本应相等，互证灯为绿
    assert before_proof["frozen_matches_live"] is True

    r = c.post(
        "/api/topup-stub",
        headers=h,
        json={"cage_id": cage["id"], "amount": 10000, "mode": "同笼扩代"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 甲路径：就地扩额，原主键/笼号不变
    assert body.get("cage_unchanged") is not True
    assert body["topup_path"] == "甲"
    assert body["formal_cage_id"] == cage["id"]
    assert body["formal_cage_code"] == "CG-P-EAST-001"

    after_cage = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    after_acct = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    assert after_cage["cage_code"] == "CG-P-EAST-001"
    assert after_cage["id"] == cage["id"]
    assert after_cage["amount"] == 130000
    assert after_cage["available"] == 130000
    assert after_cage["generation"] == 2
    assert after_acct["frozen"] == before_acct["frozen"] + 10000
    assert after_acct["balance"] == before_acct["balance"]

    after_proof = c.get("/api/proof/P-EAST", headers=h).json()
    assert after_proof["frozen_matches_live"] is True
    assert after_proof["live_cage_available"] == 130000
    assert after_proof["account"]["frozen"] == 130000

    recon = c.get("/api/reconcile/CG-P-EAST-001", headers=h).json()
    assert recon["balanced"] is True
    assert recon["amount"] == 130000
    assert recon["available"] == 130000
    assert recon["topup_total"] == 10000

    job = [
        j
        for j in c.get("/api/topup/jobs", headers=h).json()["items"]
        if j["cage_id"] == cage["id"]
    ][0]
    assert job["status"] == "扩额完成"
    assert job["generation_before"] == 1
    assert job["generation_after"] == 2
    assert job["formal_cage_code"] == "CG-P-EAST-001"

    fate = c.get("/api/topup/fate", headers=h).json()
    assert fate["path"] == "甲"
    assert fate["mode"] == "同笼扩代"
    assert fate["locked"] is True

    actions = [
        a["action"]
        for a in c.get("/api/audit?limit=200", headers=h).json()["items"]
    ]
    assert "cage_topup" in actions

    # 五页同源：账户列表与账户明细也读同一冻结数与活笼合计
    bond_row = [
        b
        for b in c.get("/api/bonds", headers=h).json()["items"]
        if b["party_key"] == "P-EAST"
    ][0]
    assert bond_row["live_cage_available"] == 130000
    detail = c.get("/api/bonds/P-EAST", headers=h).json()
    assert detail["live_cage_available"] == 130000
    assert detail["proof"]["matches"] is True

    cage_detail = c.get(f"/api/cages/{cage['id']}", headers=h).json()
    assert cage_detail["account"]["frozen"] == 130000
    assert cage_detail["party_live_available"] == 130000
    assert cage_detail["frozen_matches_live"] is True
    assert len(cage_detail["topups"]) == 1


def test_two_party_same_cage_topup_only_leaves_contention_trace():
    """两人同补同一只笼：先到持锁，后到只落争用痕，业务零写入。"""
    c = TestClient(app)
    admin = _auth(c, "admin")
    clerk = _auth(c, "clerk")
    cage = _cage(c, admin)
    lock_key = f"cage:{cage['id']}"
    assert (
        c.post(
            "/api/locks/acquire", headers=admin, json={"lock_key": lock_key}
        ).status_code
        == 200
    )
    before_cage = c.get(f"/api/cages/{cage['id']}", headers=admin).json()["cage"]
    before_acct = c.get("/api/bonds/P-EAST", headers=admin).json()["account"]
    jobs_before = len(c.get("/api/topup/jobs", headers=admin).json()["items"])

    r = c.post(
        "/api/topup-stub",
        headers=clerk,
        json={"cage_id": cage["id"], "amount": 10000, "mode": "同笼扩代"},
    )
    assert r.status_code == 409, r.text

    after_cage = c.get(f"/api/cages/{cage['id']}", headers=admin).json()["cage"]
    after_acct = c.get("/api/bonds/P-EAST", headers=admin).json()["account"]
    assert after_cage["amount"] == before_cage["amount"]
    assert after_cage["available"] == before_cage["available"]
    assert after_acct["frozen"] == before_acct["frozen"]
    assert (
        len(c.get("/api/topup/jobs", headers=admin).json()["items"]) == jobs_before
    )

    cont = c.get("/api/contention", headers=admin).json()["items"]
    assert any(
        x["lock_key"] == lock_key and x["actor"] == "clerk" for x in cont
    )
    audits = c.get("/api/audit?limit=200", headers=admin).json()["items"]
    assert not any(
        a["action"] == "cage_topup" and a["actor"] == "clerk" for a in audits
    )


def test_simultaneous_lock_acquire_on_same_cage_yields_exactly_one_holder():
    """真并发抢同一只笼锁：两条独立连接同时抢，只有一个插入成功，另一个落争用痕。"""
    import threading

    from db import connect
    from locks import try_acquire

    lock_key = "cage:seed-east"
    barrier = threading.Barrier(2)
    results = {}

    def worker(holder):
        with connect() as conn:
            barrier.wait()
            results[holder] = try_acquire(conn, lock_key, holder)
            conn.commit()

    threads = [
        threading.Thread(target=worker, args=("admin",)),
        threading.Thread(target=worker, args=("clerk",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results.values()) == [False, True], results
    winner = next(h for h, ok in results.items() if ok)
    loser = next(h for h, ok in results.items() if not ok)
    with connect() as conn:
        row = conn.execute(
            "SELECT holder FROM cage_locks WHERE lock_key=?", (lock_key,)
        ).fetchone()
        assert row["holder"] == winner
        cont = conn.execute(
            "SELECT actor FROM contention_log WHERE lock_key=?", (lock_key,)
        ).fetchall()
        assert [x["actor"] for x in cont] == [loser]


def test_simultaneous_topup_requests_never_double_expand():
    """两人同时补同一只笼：业务只扩一次，另一请求要么争用 409、要么在锁释放后按序扩额。

    TestClient 会串行处理同一进程内的请求，故这里验证「无论串行或并发」笼面都不会
    被重复扩额到超过一笔，且账户冻结与笼面始终同源。
    """
    import threading

    c = TestClient(app)
    admin = _auth(c, "admin")
    clerk = _auth(c, "clerk")
    cage = _cage(c, admin)
    state = []
    barrier = threading.Barrier(2)

    def worker(headers):
        barrier.wait()
        state.append(
            c.post(
                "/api/topup-stub",
                headers=headers,
                json={"cage_id": cage["id"], "amount": 10000, "mode": "同笼扩代"},
            )
        )

    threads = [
        threading.Thread(target=worker, args=(admin,)),
        threading.Thread(target=worker, args=(clerk,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok_count = sum(1 for r in state if r.status_code == 200)
    assert ok_count >= 1
    assert all(r.status_code in (200, 409) for r in state)
    after = c.get(f"/api/cages/{cage['id']}", headers=admin).json()["cage"]
    acct = c.get("/api/bonds/P-EAST", headers=admin).json()["account"]
    jobs = [
        j
        for j in c.get("/api/topup/jobs", headers=admin).json()["items"]
        if j["cage_id"] == cage["id"]
    ]
    # 每笔成功扩额恰好对应一笔作业票，笼面 = 基础额 + 成功笔数×1万
    assert after["amount"] == 120000 + ok_count * 10000
    assert after["available"] == after["amount"]
    assert after["cage_code"] == "CG-P-EAST-001"
    assert acct["frozen"] == after["amount"]
    assert len(jobs) == ok_count
    proof = c.get("/api/proof/P-EAST", headers=admin).json()
    assert proof["frozen_matches_live"] is True


def test_topup_on_passed_cage_invalidates_old_generation_release():
    """升代后旧代通过结论不得继续解笼；正式可解笼身份仍绑原笼主键。"""
    c = TestClient(app)
    h = _auth(c)
    cage = _cage(c, h)
    assert (
        c.post(
            "/api/attestations",
            headers=h,
            json={"cage_id": cage["id"], "outcome": "通过", "evidence": "首验"},
        ).status_code
        == 200
    )
    r = c.post(
        "/api/topup-stub",
        headers=h,
        json={"cage_id": cage["id"], "amount": 10000, "mode": "同笼扩代"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["generation_after"] == 2
    after = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    assert after["status"] == "已冻结"
    # 旧代通过不得解笼
    blocked = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert blocked.status_code == 409
    # 按新代重新核验后方可解笼，且笼号/主键不变
    assert (
        c.post(
            "/api/attestations",
            headers=h,
            json={"cage_id": cage["id"], "outcome": "通过", "evidence": "增补复核"},
        ).status_code
        == 200
    )
    mid = c.get("/api/proof/P-EAST", headers=h).json()
    assert mid["formal_cage_identity"]["cage_id"] == cage["id"]
    assert mid["formal_cage_identity"]["cage_code"] == "CG-P-EAST-001"
    assert mid["formal_cage_identity"]["generation"] == 2
    rel = c.post("/api/releases", headers=h, json={"cage_id": cage["id"]})
    assert rel.status_code == 200, rel.text
    assert rel.json()["amount"] == 130000
    assert rel.json()["cage_generation"] == 2
    proof = c.get("/api/proof/P-EAST", headers=h).json()
    assert proof["frozen_matches_live"] is True
    # 全额解笼后不再残留正式可解笼（不能对已解笼假报身份）
    assert proof["formal_cage"] is None


def test_topup_deviation_from_daily_fate_is_refused_and_recorded():
    """偏离当日命运说明（甲）改走乙路径：记扩笼偏航并拒绝写入。"""
    c = TestClient(app)
    h = _auth(c)
    cage = _cage(c, h)
    before_cage = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    before_acct = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    jobs_before = len(c.get("/api/topup/jobs", headers=h).json()["items"])

    r = c.post(
        "/api/topup-stub",
        headers=h,
        json={"cage_id": cage["id"], "amount": 10000, "mode": "新笼桥接"},
    )
    assert r.status_code == 409, r.text

    after_cage = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    after_acct = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    assert after_cage["amount"] == before_cage["amount"]
    assert after_cage["available"] == before_cage["available"]
    assert after_acct["frozen"] == before_acct["frozen"]
    assert len(c.get("/api/topup/jobs", headers=h).json()["items"]) == jobs_before

    audits = c.get("/api/audit?limit=200", headers=h).json()["items"]
    assert any(a["action"] == "topup_deviation" for a in audits)


def test_topup_rolls_back_all_business_writes_on_late_failure(monkeypatch):
    """任一步失败：笼面与账户冻结整笔回滚，不留作业票半截。"""
    import topup_svc

    c = TestClient(app)
    h = _auth(c)
    cage = _cage(c, h)
    before_cage = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    before_acct = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    jobs_before = len(c.get("/api/topup/jobs", headers=h).json()["items"])

    def boom(*_a, **_k):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(topup_svc, "write_audit", boom)
    with pytest.raises(RuntimeError):
        c.post(
            "/api/topup-stub",
            headers=h,
            json={"cage_id": cage["id"], "amount": 10000, "mode": "同笼扩代"},
        )

    after_cage = c.get(f"/api/cages/{cage['id']}", headers=h).json()["cage"]
    after_acct = c.get("/api/bonds/P-EAST", headers=h).json()["account"]
    assert after_cage["amount"] == before_cage["amount"]
    assert after_cage["available"] == before_cage["available"]
    assert after_cage["generation"] == before_cage["generation"]
    assert after_acct["frozen"] == before_acct["frozen"]
    assert len(c.get("/api/topup/jobs", headers=h).json()["items"]) == jobs_before
    proof = c.get("/api/proof/P-EAST", headers=h).json()
    assert proof["frozen_matches_live"] is True
