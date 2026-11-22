"""增补入笼（甲路径：同笼扩额整笔落库，ADR-2026-11-22-TOPUP-01 当日锁死）。

口径（详见 docs/decisions/增补扩笼纪要.md）：
- 只接受 mode="同笼扩代"；"新笼桥接"（乙路径）记扩笼偏航并拒绝；
- 抢笼锁 cage:{cage_id}，后到者只留 contention 争用痕，不写业务；
- 单连接单事务：作业票 → 笼面抬高+笼代+1 → 账户冻结增加（balance 不动）→ 审计；
- 任一步失败整笔 rollback，笼与账户零改。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import TopupIn

router = APIRouter(tags=["topup"])

# 纪要锁死：仅甲路径
LOCKED_MODE = "同笼扩代"
DEVIATION_MODE = "新笼桥接"
LIVE_CAGE_STATUS = (
    "已冻结",
    "核验通过",
    "核验违约",
    "部分解笼",
    "部分罚没",
)


@router.get("/api/topup/jobs")
def list_jobs(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT j.*, c.cage_code FROM topup_jobs j
               JOIN cages c ON c.id=j.cage_id
               ORDER BY j.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/topup")
def topup_expand(body: TopupIn, authorization: AuthHeader = None):
    """同笼扩额整笔落库：把当事人可用保证金真正冻进本笼。"""
    user = require_writer(authorization)

    # 偏航口径：乙路径（新笼桥接）只记偏航审计并拒绝，零业务写入
    if body.mode != LOCKED_MODE:
        with connect() as dev_conn:
            write_audit(
                dev_conn,
                user,
                "topup_deviation",
                f"cage_id={body.cage_id}",
                {
                    "requested_mode": body.mode,
                    "locked_mode": LOCKED_MODE,
                    "note": "扩笼偏航：纪要锁死甲路径，拒绝乙路径/非法口径",
                },
            )
            dev_conn.commit()
        if body.mode == DEVIATION_MODE:
            raise HTTPException(400, "扩笼偏航：影子桥接已被增补扩笼纪要禁用")
        raise HTTPException(400, "mode 须为同笼扩代（纪要锁死）")

    lock_key = f"cage:{body.cage_id}"
    conn = connect()
    try:
        # 后到者：require_lock 内部已写 contention_log，随失败响应不提交业务数据
        require_lock(conn, lock_key, user)
        try:
            cage = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            if not cage:
                raise HTTPException(404, "笼不存在")
            if cage["status"] not in LIVE_CAGE_STATUS:
                raise HTTPException(409, "当前笼状态禁止增补扩额")

            acct = conn.execute(
                "SELECT * FROM bond_accounts WHERE party_key=?",
                (cage["party_key"],),
            ).fetchone()
            if not acct:
                raise HTTPException(404, "保证金户不存在")
            if acct["status"] != "正常":
                raise HTTPException(409, "账户状态禁止增补")

            amount = float(body.amount)
            free = float(acct["balance"]) - float(acct["frozen"])
            if amount > free + 1e-9:
                raise HTTPException(409, "可用保证金不足，无法增补入笼")

            now = now_iso()
            gen_before = int(cage["generation"])
            gen_after = gen_before + 1
            detail = (
                f"同笼扩额整笔落库：g{gen_before}→g{gen_after}；"
                f"笼可用 {float(cage['available']):.2f}→"
                f"{float(cage['available']) + amount:.2f}；账户冻结同抬"
            )
            cur = conn.execute(
                """INSERT INTO topup_jobs(cage_id,amount,mode,status,detail,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (body.cage_id, amount, LOCKED_MODE, "已扩笼", detail, now),
            )
            job_id = cur.lastrowid

            # 笼面：总额与可用同抬，笼代推进
            conn.execute(
                """UPDATE cages
                   SET amount = amount + ?,
                       available = available + ?,
                       generation = generation + 1
                   WHERE id=?""",
                (amount, amount, body.cage_id),
            )
            # 账户：可用→冻结（balance 不动，钱不出系统）
            conn.execute(
                "UPDATE bond_accounts SET frozen = frozen + ? WHERE party_key=?",
                (amount, cage["party_key"]),
            )
            write_audit(
                conn,
                user,
                "topup_expand",
                cage["cage_code"],
                {
                    "job_id": job_id,
                    "amount": amount,
                    "mode": LOCKED_MODE,
                    "generation_before": gen_before,
                    "generation_after": gen_after,
                    "party_key": cage["party_key"],
                },
            )
            conn.commit()

            row = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            return {
                "job_id": job_id,
                "status": "已扩笼",
                "mode": LOCKED_MODE,
                "cage_id": body.cage_id,
                "cage_code": cage["cage_code"],
                "amount": float(row["amount"]),
                "available": float(row["available"]),
                "generation": int(row["generation"]),
            }
        except HTTPException:
            # 业务校验失败：整笔回滚，笼与账户零改
            conn.rollback()
            raise
        except Exception:
            # 任一步意外失败：作业票、笼面、冻结一并回滚
            conn.rollback()
            raise HTTPException(500, "增补扩笼失败，已整笔回滚")
    finally:
        try:
            release(conn, lock_key, user)
            conn.commit()
        finally:
            conn.close()
