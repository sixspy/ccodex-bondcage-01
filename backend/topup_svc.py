"""增补入笼（甲路径：同笼主键就地扩额）。

命运说明：当日唯一口径为甲路径——同一提交整笔改写原笼行总额与可用、抬账户冻结、
升代并记审计，笼主键与对外笼号保持不变；任一步失败账户冻结与笼面整笔回滚。
乙路径（封存旧笼再开新笼）今日不采用，偏离记「扩笼偏航」并拒绝写入。禁止第三路。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import (
    SEALED_CAGE_STATUSES,
    connect,
    ensure_fate,
    now_iso,
    today_str,
    write_audit,
)
from locks import release, require_lock
from models import TopupIn

router = APIRouter(tags=["topup"])

# 增补只允许在尚未得出核验结论的活笼上扩额，避免与核验/解笼竞态。
TOPUP_ALLOWED_STATUSES = ("已冻结", "核验通过")


class FateIn(BaseModel):
    path: str = "甲"
    mode: str = "同笼扩代"


def _log_deviation(user: str, cage_id: int, mode: str, fate: dict) -> None:
    """扩笼偏航须留痕且不随业务回滚消失：用独立连接立即落库。"""
    with connect() as log_conn:
        write_audit(
            log_conn,
            user,
            "topup_deviation",
            f"cage:{cage_id}",
            {
                "requested_mode": mode,
                "fate_path": fate["path"],
                "fate_mode": fate["mode"],
                "fate_date": fate["fate_date"],
                "note": "偏离当日命运说明，拒绝写入",
            },
        )
        log_conn.commit()


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


@router.get("/api/topup/fate")
def get_fate(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        fate = ensure_fate(conn)
        conn.commit()
        return {
            "fate_date": fate["fate_date"],
            "path": fate["path"],
            "mode": fate["mode"],
            "note": fate["note"],
            "locked": bool(fate["locked"]),
            "today": today_str(),
        }


@router.post("/api/topup/fate")
def lock_fate(body: FateIn, authorization: AuthHeader = None):
    """命运说明当日锁死：只回显，不接受任何改写。"""
    user = require_writer(authorization)
    with connect() as conn:
        fate = ensure_fate(conn)
        conn.commit()
        if body.path != fate["path"] or body.mode != fate["mode"]:
            write_audit(
                conn,
                user,
                "topup_deviation",
                "fate",
                {
                    "requested_path": body.path,
                    "requested_mode": body.mode,
                    "fate_path": fate["path"],
                    "fate_mode": fate["mode"],
                    "note": "命运说明当日锁死，拒绝改写",
                },
            )
            conn.commit()
            raise HTTPException(409, "当日命运说明已锁死，禁止改写路径")
        return {
            "fate_date": fate["fate_date"],
            "path": fate["path"],
            "mode": fate["mode"],
            "note": fate["note"],
            "locked": True,
        }


@router.post("/api/topup-stub")
def topup_cage(body: TopupIn, authorization: AuthHeader = None):
    """甲路径：同笼就地扩额。保留旧路由名以免页面入口另起。"""
    user = require_writer(authorization)
    amount = float(body.amount)
    if amount <= 0:
        raise HTTPException(400, "增补金额须大于 0")

    with connect() as conn:
        fate = ensure_fate(conn)
        conn.commit()
        # 偏离当日命运说明：记偏航并拒绝写入（不得静默走乙路径或第三路）
        if body.mode != fate["mode"] or fate["path"] != "甲":
            _log_deviation(user, body.cage_id, body.mode, fate)
            raise HTTPException(
                409,
                f"扩笼偏航：当日仅允许{fate['path']}路径「{fate['mode']}」，"
                f"收到「{body.mode}」，拒绝写入",
            )

        lock_key = f"cage:{body.cage_id}"
        require_lock(conn, lock_key, user)
        try:
            cage = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            if not cage:
                raise HTTPException(404, "笼不存在")
            if cage["status"] in SEALED_CAGE_STATUSES:
                raise HTTPException(409, "旧笼已封存，只能读历史，不得再作增补目标")
            if cage["status"] not in TOPUP_ALLOWED_STATUSES:
                raise HTTPException(
                    409, "当前笼状态禁止增补扩额（仅已冻结/核验通过可增补）"
                )

            acct = conn.execute(
                "SELECT * FROM bond_accounts WHERE party_key=?",
                (cage["party_key"],),
            ).fetchone()
            if not acct:
                raise HTTPException(404, "当事人保证金户不存在")
            if acct["status"] != "正常":
                raise HTTPException(409, "账户状态禁止增补")
            free = float(acct["balance"]) - float(acct["frozen"])
            if amount > free + 1e-9:
                raise HTTPException(409, "账户可用额不足，无法增补")

            gen_before = int(cage["generation"])
            gen_after = gen_before + 1
            now = now_iso()
            topup_ref = f"TOP-{cage['cage_code']}-g{gen_before}->g{gen_after}"
            detail = (
                f"甲路径同笼扩额，笼主键 {cage['id']} 与笼号 {cage['cage_code']} 不变，"
                f"代 {gen_before}→{gen_after}"
            )

            # 整笔改写：原笼行总额/可用/升代；账户冻结同笔抬升；作业票与审计同笔写入。
            # 升代后旧代核验结论自动失效：笼状态回落「已冻结」，须按新代重新核验，
            # 防止解笼仍绑旧代的通过结论而假绿。
            new_status = "已冻结" if cage["status"] == "核验通过" else cage["status"]
            conn.execute(
                """UPDATE cages
                   SET amount = amount + ?, available = available + ?, generation = ?,
                       status = ?
                   WHERE id=?""",
                (amount, amount, gen_after, new_status, body.cage_id),
            )
            conn.execute(
                "UPDATE bond_accounts SET frozen = frozen + ? WHERE party_key=?",
                (amount, cage["party_key"]),
            )
            cur = conn.execute(
                """INSERT INTO topup_jobs(
                       cage_id,amount,mode,status,detail,created_at,
                       generation_before,generation_after,
                       formal_cage_id,formal_cage_code,topup_ref,fate_path)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    body.cage_id,
                    amount,
                    body.mode,
                    "扩额完成",
                    detail,
                    now,
                    gen_before,
                    gen_after,
                    body.cage_id,
                    cage["cage_code"],
                    topup_ref,
                    fate["path"],
                ),
            )
            job_id = cur.lastrowid
            write_audit(
                conn,
                user,
                "cage_topup",
                cage["cage_code"],
                {
                    "job_id": job_id,
                    "topup_ref": topup_ref,
                    "amount": amount,
                    "path": fate["path"],
                    "mode": body.mode,
                    "cage_id": body.cage_id,
                    "cage_code_unchanged": cage["cage_code"],
                    "amount_before": float(cage["amount"]),
                    "amount_after": float(cage["amount"]) + amount,
                    "available_before": float(cage["available"]),
                    "available_after": float(cage["available"]) + amount,
                    "account_frozen_before": float(acct["frozen"]),
                    "account_frozen_after": float(acct["frozen"]) + amount,
                    "generation_before": gen_before,
                    "generation_after": gen_after,
                    "status_before": cage["status"],
                    "status_after": new_status,
                    "fate_date": fate["fate_date"],
                },
            )
            # 提交前复核：主键/笼号不变、三个数额同向增长、代已升；任何不符整笔回滚。
            after = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            after_acct = conn.execute(
                "SELECT * FROM bond_accounts WHERE party_key=?",
                (cage["party_key"],),
            ).fetchone()
            if (
                after["cage_code"] != cage["cage_code"]
                or int(after["generation"]) != gen_after
                or after["status"] != new_status
                or abs(float(after["amount"]) - (float(cage["amount"]) + amount)) > 1e-6
                or abs(float(after["available"]) - (float(cage["available"]) + amount))
                > 1e-6
                or abs(float(after_acct["frozen"]) - (float(acct["frozen"]) + amount))
                > 1e-6
            ):
                conn.rollback()
                raise HTTPException(500, "增补后复核失败：笼面/账户未同笔扩额")
            conn.commit()

            return {
                "job_id": job_id,
                "status": "扩额完成",
                "topup_path": fate["path"],
                "mode": body.mode,
                "cage_unchanged": False,
                "formal_cage_id": body.cage_id,
                "formal_cage_code": cage["cage_code"],
                "amount": float(after["amount"]),
                "available": float(after["available"]),
                "generation_before": gen_before,
                "generation_after": int(after["generation"]),
                "account_frozen": float(after_acct["frozen"]),
                "topup_ref": topup_ref,
                "fate_locked": True,
            }
        except HTTPException:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            release(conn, lock_key, user)
            conn.commit()
