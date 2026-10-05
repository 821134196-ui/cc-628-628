"""订单：创建（逐件按序列号）、查询、关闭（释放剩余押金）、押金台账。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (ASSET_AVAILABLE, ASSET_RESERVED, DISPUTE_OPEN,
                      DISPUTE_REINSPECTED, ITEM_RETURNED, ITEM_SWAPPED,
                      Asset, Customer, DepositTransaction, Dispute, OrderItem,
                      RentalOrder)
from ..schemas import OrderCreate
from ..serialize import order_dict

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("")
def create_order(body: OrderCreate, db: Session = Depends(get_db)):
    customer = db.get(Customer, body.customer_id)
    if not customer:
        raise HTTPException(404, "客户不存在")
    if len(set(body.serials)) != len(body.serials):
        raise HTTPException(400, "序列号重复")

    assets = []
    for serial in body.serials:
        a = db.query(Asset).filter(Asset.serial == serial).first()
        if not a:
            raise HTTPException(404, f"资产不存在: {serial}")
        if a.status != ASSET_AVAILABLE:
            raise HTTPException(409, f"{serial} 当前状态为 {a.status}，不可出租")
        assets.append(a)

    order = RentalOrder(customer_id=customer.id, deposit_amount=body.deposit_amount)
    db.add(order)
    db.flush()
    for slot, asset in enumerate(assets, start=1):
        asset.status = ASSET_RESERVED
        db.add(OrderItem(order_id=order.id, asset_id=asset.id, slot=slot))
    db.commit()
    db.refresh(order)
    return order_dict(order)


@router.get("")
def list_orders(db: Session = Depends(get_db)):
    orders = db.query(RentalOrder).order_by(RentalOrder.id.desc()).all()
    return [order_dict(o, with_items=False) for o in orders]


@router.get("/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(RentalOrder, order_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    return order_dict(order)


@router.get("/{order_id}/deposit")
def deposit_ledger(order_id: int, db: Session = Depends(get_db)):
    """押金台账：总额 / 冻结 / 已扣 / 已释放 + 全部流水。"""
    order = db.get(RentalOrder, order_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    txns = (db.query(DepositTransaction)
            .filter(DepositTransaction.order_id == order_id)
            .order_by(DepositTransaction.id).all())
    return {
        "order_id": order.id,
        "deposit_amount": order.deposit_amount,
        "frozen_amount": order.frozen_amount,
        "deducted_amount": order.deducted_amount,
        "released_amount": order.released_amount,
        "transactions": [
            {"id": t.id, "type": t.type, "amount": t.amount, "reason": t.reason,
             "idempotency_key": t.idempotency_key,
             "created_at": t.created_at.isoformat() if t.created_at else None}
            for t in txns
        ],
    }


@router.post("/{order_id}/close")
def close_order(order_id: int, db: Session = Depends(get_db)):
    """全部件归还且争议结案后关闭订单，释放剩余押金。"""
    order = db.get(RentalOrder, order_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status == "closed":
        raise HTTPException(409, "订单已关闭")
    for it in order.items:
        if it.status not in (ITEM_RETURNED, ITEM_SWAPPED):
            raise HTTPException(409, f"槽位 {it.slot} 的设备尚未归还，不能关闭订单")
    open_disputes = (db.query(Dispute).join(OrderItem)
                     .filter(OrderItem.order_id == order_id,
                             Dispute.status.in_([DISPUTE_OPEN, DISPUTE_REINSPECTED]))
                     .count())
    if open_disputes:
        raise HTTPException(409, "存在未结案的争议，不能关闭订单")

    remaining = (order.deposit_amount - order.frozen_amount
                 - order.deducted_amount - order.released_amount)
    if remaining > 0:
        key = f"release-order-close-{order.id}"
        if not db.query(DepositTransaction).filter_by(idempotency_key=key).first():
            db.add(DepositTransaction(
                order_id=order.id, type="release", amount=remaining,
                reason="订单关闭，释放剩余押金", idempotency_key=key))
            order.released_amount += remaining
    order.status = "closed"
    db.commit()
    return order_dict(order)
