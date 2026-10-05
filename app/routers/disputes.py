"""暗伤争议：独立复检（复检人≠原出库员工）、定损结算（幂等，不重复扣款）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (ASSET_AVAILABLE, ASSET_MAINTENANCE, DISPUTE_OPEN,
                      DISPUTE_REINSPECTED, DISPUTE_REJECTED, DISPUTE_SETTLED,
                      ITEM_RETURNED, DepositTransaction, Dispute, ReInspection,
                      Settlement)
from ..schemas import ReinspectIn, SettleIn
from ..serialize import dispute_dict

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


def _get_dispute(db: Session, dispute_id: int) -> Dispute:
    d = db.get(Dispute, dispute_id)
    if not d:
        raise HTTPException(404, "争议不存在")
    return d


@router.post("/{dispute_id}/reinspect")
def reinspect(dispute_id: int, body: ReinspectIn, db: Session = Depends(get_db)):
    """复检：必须由非原出库员工执行。"""
    d = _get_dispute(db, dispute_id)
    if d.status != DISPUTE_OPEN:
        raise HTTPException(409, f"争议当前状态为 {d.status}，不能复检")

    item = d.order_item
    original_employee_id = item.checkout.employee_id if item.checkout else None
    if body.inspector_id == original_employee_id:
        raise HTTPException(403, "复检人不得为该件的原出库员工（需独立复检）")

    db.add(ReInspection(dispute_id=d.id, inspector_id=body.inspector_id,
                        damage_confirmed=body.damage_confirmed,
                        notes=body.notes))
    order = item.order
    if body.damage_confirmed:
        d.status = DISPUTE_REINSPECTED  # 等待定损结算，资金保持冻结
    else:
        # 复检驳回：解除冻结，设备与押金恢复正常
        d.status = DISPUTE_REJECTED
        item.status = ITEM_RETURNED
        item.asset.status = ASSET_AVAILABLE
        key = f"release-dispute-{d.id}"
        if not db.query(DepositTransaction).filter_by(idempotency_key=key).first():
            db.add(DepositTransaction(
                order_id=order.id, type="release", amount=d.freeze_amount,
                reason=f"争议 #{d.id} 复检驳回，解除冻结", idempotency_key=key))
            order.frozen_amount -= d.freeze_amount
            order.released_amount += d.freeze_amount
    db.commit()
    return dispute_dict(d)


@router.post("/{dispute_id}/settle")
def settle(dispute_id: int, body: SettleIn, db: Session = Depends(get_db)):
    """定损结算：扣减原因逐项入账；幂等——重复请求不会重复扣款。"""
    d = _get_dispute(db, dispute_id)

    # 幂等：同一 idempotency_key 直接返回原结算单，不再扣款
    existing = (db.query(Settlement)
                .filter(Settlement.idempotency_key == body.idempotency_key)
                .first())
    if existing:
        return {"duplicate": True, "dispute": dispute_dict(d),
                "settlement_dispute_id": existing.dispute_id}

    if d.status == DISPUTE_SETTLED:
        raise HTTPException(409, "该争议已结算，不能重复扣款")
    if d.status != DISPUTE_REINSPECTED:
        raise HTTPException(409, f"争议当前状态为 {d.status}，须先复检确认损伤")

    total = round(sum(i.amount for i in body.items), 2)
    if total <= 0:
        raise HTTPException(400, "扣减总额必须大于 0")
    if total > d.freeze_amount + 1e-9:
        raise HTTPException(400, f"扣减总额 {total} 超过冻结金额 {d.freeze_amount}")

    order = d.order_item.order
    # 扣减：冻结 → 已扣
    db.add(DepositTransaction(
        order_id=order.id, type="deduct", amount=total,
        reason=f"争议 #{d.id} 定损扣减：" +
               "；".join(f"{i.reason}¥{i.amount}" for i in body.items),
        idempotency_key=body.idempotency_key))
    order.frozen_amount -= total
    order.deducted_amount += total
    # 剩余冻结资金释放
    remainder = round(d.freeze_amount - total, 2)
    if remainder > 0:
        db.add(DepositTransaction(
            order_id=order.id, type="release", amount=remainder,
            reason=f"争议 #{d.id} 结算后释放剩余冻结",
            idempotency_key=f"release-dispute-{d.id}"))
        order.frozen_amount -= remainder
        order.released_amount += remainder

    db.add(Settlement(
        dispute_id=d.id, idempotency_key=body.idempotency_key,
        items=[{"reason": i.reason, "amount": i.amount} for i in body.items],
        total=total))
    d.status = DISPUTE_SETTLED
    d.order_item.status = ITEM_RETURNED
    d.order_item.asset.status = ASSET_MAINTENANCE  # 暗伤设备转维修，不进可用库存
    db.commit()
    return {"duplicate": False, "dispute": dispute_dict(d)}
