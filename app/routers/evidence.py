"""客户证据页：同一序列号出库/归还证据并排；换件后按槽位追溯正确出库证据。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import RentalOrder, ReturnRecord
from ..serialize import item_dict, order_dict, return_dict

router = APIRouter(prefix="/api/orders", tags=["evidence"])


@router.get("/{order_id}/evidence")
def order_evidence(order_id: int, db: Session = Depends(get_db)):
    order = db.get(RentalOrder, order_id)
    if not order:
        raise HTTPException(404, "订单不存在")

    by_slot = {}
    for it in order.items:
        by_slot.setdefault(it.slot, []).append(it)

    slots = []
    for slot, items in sorted(by_slot.items()):
        items.sort(key=lambda x: x.id)
        current = items[-1]  # 换件链条上的最新一件
        slots.append({
            "slot": slot,
            "current_serial": current.asset.serial,
            "swapped": len(items) > 1,
            "items": [item_dict(it, {current.id}) for it in items],
        })

    # 错还拦截记录（无归属件）也一并展示
    wrong_returns = [return_dict(r) for r in
                     db.query(ReturnRecord)
                     .filter(ReturnRecord.order_id == order_id,
                             ReturnRecord.order_item_id.is_(None))
                     .order_by(ReturnRecord.id)]

    return {"order": order_dict(order, with_items=False),
            "slots": slots, "wrong_returns": wrong_returns}
