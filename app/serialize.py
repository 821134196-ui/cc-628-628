"""对象 → 字典 的序列化辅助。"""
from .models import (Dispute, OrderItem, ReInspection, ReturnRecord,
                     Settlement)


def asset_dict(a):
    return {"id": a.id, "serial": a.serial, "category": a.category,
            "model": a.model, "status": a.status}


def employee_dict(e):
    return {"id": e.id, "name": e.name}


def checkout_dict(c):
    if c is None:
        return None
    return {
        "employee_id": c.employee_id,
        "employee_name": c.employee.name if c.employee else None,
        "serial_scanned": c.serial_scanned,
        "photos": c.photos or [],
        "function_tests": c.function_tests or {},
        "notes": c.notes,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def return_dict(r: ReturnRecord):
    return {
        "id": r.id,
        "employee_id": r.employee_id,
        "employee_name": r.employee.name if r.employee else None,
        "serial_scanned": r.serial_scanned,
        "result": r.result,
        "photos": r.photos or [],
        "condition_notes": r.condition_notes,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def settlement_dict(s: Settlement):
    if s is None:
        return None
    return {"items": s.items or [], "total": s.total,
            "idempotency_key": s.idempotency_key,
            "created_at": s.created_at.isoformat() if s.created_at else None}


def reinspection_dict(ri: ReInspection):
    if ri is None:
        return None
    return {
        "inspector_id": ri.inspector_id,
        "inspector_name": ri.inspector.name if ri.inspector else None,
        "damage_confirmed": ri.damage_confirmed,
        "notes": ri.notes,
        "created_at": ri.created_at.isoformat() if ri.created_at else None,
    }


def dispute_dict(d: Dispute):
    return {
        "id": d.id,
        "order_item_id": d.order_item_id,
        "description": d.description,
        "status": d.status,
        "freeze_amount": d.freeze_amount,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "reinspection": reinspection_dict(d.reinspection),
        "settlement": settlement_dict(d.settlement),
    }


def item_dict(it: OrderItem, current_item_ids=None):
    return {
        "item_id": it.id,
        "slot": it.slot,
        "status": it.status,
        "replaces_item_id": it.replaces_item_id,
        "is_current": current_item_ids is None or it.id in current_item_ids,
        "asset": asset_dict(it.asset),
        "checkout": checkout_dict(it.checkout),
        "returns": [return_dict(r) for r in it.returns],
        "dispute": dispute_dict(it.dispute) if it.dispute else None,
    }


def order_dict(o, with_items=True):
    d = {
        "id": o.id,
        "status": o.status,
        "customer_id": o.customer_id,
        "customer_name": o.customer.name if o.customer else None,
        "deposit_amount": o.deposit_amount,
        "frozen_amount": o.frozen_amount,
        "deducted_amount": o.deducted_amount,
        "released_amount": o.released_amount,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }
    if with_items:
        # 每个 slot 的"当前件"= 该 slot 下未被替换的最新一件
        current_ids = set()
        by_slot = {}
        for it in o.items:
            by_slot.setdefault(it.slot, []).append(it)
        for slot_items in by_slot.values():
            current = max(slot_items, key=lambda x: x.id)
            current_ids.add(current.id)
        d["items"] = [item_dict(it, current_ids)
                      for it in sorted(o.items, key=lambda x: (x.slot, x.id))]
    return d
