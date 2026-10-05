"""模拟押金支付通道 + 押金账本（冻结/扣减/退款，幂等）。

账本约定：
- paid      = payment 之和
- frozen    = freeze - unfreeze（争议未决时对应资金被冻结）
- deducted  = deduct 之和
- refunded  = refund 之和
恒等式：paid = available + frozen + deducted + refunded
可用余额 available = paid - frozen - deducted - refunded >= 0
"""
import secrets
from sqlalchemy import func
from sqlalchemy.orm import Session
from fastapi import HTTPException

from . import models


def _sum(db: Session, order_id: int, txn_type: str) -> float:
    v = db.query(func.coalesce(func.sum(models.DepositTxn.amount), 0.0)).filter(
        models.DepositTxn.order_id == order_id,
        models.DepositTxn.txn_type == txn_type,
    ).scalar()
    return float(v or 0.0)


def balances(db: Session, order_id: int) -> dict:
    paid = _sum(db, order_id, models.TxnType.PAYMENT)
    frozen = (_sum(db, order_id, models.TxnType.FREEZE)
              - _sum(db, order_id, models.TxnType.UNFREEZE))
    deducted = _sum(db, order_id, models.TxnType.DEDUCT)
    refunded = _sum(db, order_id, models.TxnType.REFUND)
    available = paid - frozen - deducted - refunded
    return {
        "paid": round(paid, 2),
        "frozen": round(frozen, 2),
        "deducted": round(deducted, 2),
        "refunded": round(refunded, 2),
        "available": round(available, 2),
    }


# ---------- 模拟支付网关 ----------
def mock_gateway_charge(amount: float, ref: str | None = None) -> dict:
    """模拟调用第三方支付：永远成功，返回通道流水。"""
    return {
        "gateway_ref": ref or f"GW-{secrets.token_hex(8).upper()}",
        "amount": round(amount, 2),
        "status": "succeeded",
    }


def pay_deposit(db: Session, order: models.Order, amount: float | None,
                gateway_ref: str | None, staff_id: int | None = None) -> models.DepositTxn:
    amount = order.deposit_required if amount is None else amount
    if amount <= 0:
        raise HTTPException(400, "支付金额必须大于 0")
    gw = mock_gateway_charge(amount, gateway_ref)
    db.add(models.DepositPayment(
        order_id=order.id, amount=amount,
        gateway_ref=gw["gateway_ref"], status=gw["status"],
    ))
    txn = models.DepositTxn(
        order_id=order.id, txn_type=models.TxnType.PAYMENT, amount=amount,
        reason=f"模拟支付通道入账 {gw['gateway_ref']}", staff_id=staff_id,
    )
    db.add(txn)
    return txn


def _add(db: Session, order_id: int, txn_type: str, amount: float,
         reason: str, staff_id: int | None = None, dispute_id: int | None = None,
         idempotency_key: str | None = None) -> models.DepositTxn:
    txn = models.DepositTxn(
        order_id=order_id, dispute_id=dispute_id, txn_type=txn_type,
        amount=round(amount, 2), reason=reason, staff_id=staff_id,
        idempotency_key=idempotency_key,
    )
    db.add(txn)
    db.flush()
    return txn


def freeze_for_dispute(db: Session, dispute: models.DefectDispute,
                       staff_id: int) -> models.DepositTxn:
    """争议未决先冻结对应资金（冻结额不超过可用余额）。"""
    bal = balances(db, dispute.order_id)
    if bal["available"] <= 0:
        raise HTTPException(400, "押金可用余额不足，无法冻结")
    amount = min(dispute.hold_amount, bal["available"])
    if amount <= 0:
        raise HTTPException(400, "冻结金额必须大于 0")
    return _add(db, dispute.order_id, models.TxnType.FREEZE, amount,
                f"争议#{dispute.id} 冻结押金，待非原出库员工复检", staff_id,
                dispute_id=dispute.id)


def settle_dispute(db: Session, dispute: models.DefectDispute,
                   staff_id: int, idempotency_key: str | None) -> models.DepositTxn:
    """按报价逐项扣减。重复扣款请求不得再次扣减：

    1) 同一争议已有 deduct 记录 -> 直接返回原记录（幂等成功，不二次扣减）；
    2) 相同 idempotency_key -> 返回原记录；
    3) 否则执行扣减（优先使用已冻结资金，不足部分从可用余额补扣）。
    """
    existing = db.query(models.DepositTxn).filter(
        models.DepositTxn.dispute_id == dispute.id,
        models.DepositTxn.txn_type == models.TxnType.DEDUCT,
    ).first()
    if existing:
        return existing  # 重复请求：不再次扣减

    if idempotency_key:
        same_key = db.query(models.DepositTxn).filter(
            models.DepositTxn.idempotency_key == idempotency_key
        ).first()
        if same_key:
            return same_key

    total = sum(q.amount for q in dispute.quotes)
    if total <= 0:
        raise HTTPException(400, "没有报价项目，无法扣减")
    bal = balances(db, dispute.order_id)
    if bal["available"] + bal["frozen"] < total - 1e-9:
        raise HTTPException(400, "押金余额不足以覆盖扣减金额")

    # 先解除本争议冻结，再按总额扣减，账目逐项可查
    frozen_for_this = db.query(func.coalesce(func.sum(models.DepositTxn.amount), 0.0)).filter(
        models.DepositTxn.dispute_id == dispute.id,
        models.DepositTxn.txn_type == models.TxnType.FREEZE,
    ).scalar() or 0.0
    if frozen_for_this:
        _add(db, dispute.order_id, models.TxnType.UNFREEZE, float(frozen_for_this),
             f"争议#{dispute.id} 复检确认暗伤，解冻转扣减", staff_id,
             dispute_id=dispute.id)
    reasons = "; ".join(f"{q.reason} ¥{q.amount:.2f}" for q in dispute.quotes)
    txn = _add(db, dispute.order_id, models.TxnType.DEDUCT, total,
               f"争议#{dispute.id} 扣减：{reasons}", staff_id,
               dispute_id=dispute.id, idempotency_key=idempotency_key)
    return txn


def release_freeze(db: Session, dispute: models.DefectDispute,
                   staff_id: int) -> models.DepositTxn | None:
    """复检驳回暗伤：解除该争议冻结。"""
    frozen_for_this = db.query(func.coalesce(func.sum(models.DepositTxn.amount), 0.0)).filter(
        models.DepositTxn.dispute_id == dispute.id,
        models.DepositTxn.txn_type == models.TxnType.FREEZE,
    ).scalar() or 0.0
    if not frozen_for_this:
        return None
    return _add(db, dispute.order_id, models.TxnType.UNFREEZE, float(frozen_for_this),
                f"争议#{dispute.id} 复检驳回，解除冻结", staff_id,
                dispute_id=dispute.id)


def refund_remaining(db: Session, order: models.Order, staff_id: int) -> models.DepositTxn | None:
    """订单结清：退还剩余可用余额。"""
    bal = balances(db, order.id)
    if bal["available"] <= 0:
        return None
    return _add(db, order.id, models.TxnType.REFUND, bal["available"],
                "订单结清，退还剩余押金", staff_id)
