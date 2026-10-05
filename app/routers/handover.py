"""交接：出库登记、归还对照（含错还拦截）、套装换件（重新办理交接）。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (ASSET_AVAILABLE, ASSET_QUARANTINED, ASSET_RENTED,
                      ITEM_DISPUTED, ITEM_OUT, ITEM_PENDING, ITEM_RETURNED,
                      ITEM_SWAPPED, RET_DAMAGE, RET_INTACT, RET_WRONG, Asset,
                      CheckoutRecord, DepositTransaction, Dispute, Employee,
                      OrderItem, RentalOrder, ReturnRecord)
from ..schemas import CheckoutIn, ReturnIn, SwapIn
from ..serialize import item_dict, order_dict

router = APIRouter(prefix="/api/orders/{order_id}", tags=["handover"])


def _get_order(db: Session, order_id: int) -> RentalOrder:
    order = db.get(RentalOrder, order_id)
    if not order:
        raise HTTPException(404, "订单不存在")
    if order.status == "closed":
        raise HTTPException(409, "订单已关闭")
    return order


def _check_employee(db: Session, employee_id: int):
    if not db.get(Employee, employee_id):
        raise HTTPException(404, "员工不存在")


def _validate_handover_evidence(photos, function_tests):
    """交接证据完整性：至少一张外观照片 + 对焦测试通过。"""
    if not photos:
        raise HTTPException(400, "交接必须拍摄外观照片")
    if not function_tests.get("focus"):
        raise HTTPException(400, "对焦功能测试未通过或未记录，不能办理交接")


@router.post("/checkout")
def checkout(order_id: int, body: CheckoutIn, db: Session = Depends(get_db)):
    """出库：逐件核对序列号，拍照并记录对焦等功能测试。"""
    order = _get_order(db, order_id)
    _check_employee(db, body.employee_id)
    _validate_handover_evidence(body.photos, body.function_tests)

    item = db.get(OrderItem, body.item_id)
    if not item or item.order_id != order.id:
        raise HTTPException(404, "订单项不存在")
    if item.status != ITEM_PENDING:
        raise HTTPException(409, f"该件当前状态为 {item.status}，不能重复出库")
    if body.serial_scanned != item.asset.serial:
        raise HTTPException(
            400, f"序列号不符：订单绑定 {item.asset.serial}，"
                 f"实扫 {body.serial_scanned}，出库被拦截")

    db.add(CheckoutRecord(
        order_item_id=item.id, employee_id=body.employee_id,
        serial_scanned=body.serial_scanned, photos=body.photos,
        function_tests=body.function_tests, notes=body.notes))
    item.status = ITEM_OUT
    item.asset.status = ASSET_RENTED
    db.commit()
    return order_dict(order)


@router.post("/returns")
def return_item(order_id: int, body: ReturnIn, db: Session = Depends(get_db)):
    """归还：逐件与出库记录对照。序列号不符 → 错还拦截，设备隔离不入库。"""
    order = _get_order(db, order_id)
    _check_employee(db, body.employee_id)

    # 找本订单中"在租"且序列号匹配的件
    item = None
    for it in order.items:
        if it.status == ITEM_OUT and it.asset.serial == body.serial_scanned:
            item = it
            break

    if item is None:
        # 是否本订单的件但已归还/已换件？
        known = [it for it in order.items if it.asset.serial == body.serial_scanned]
        if known:
            raise HTTPException(
                409, f"{body.serial_scanned} 在本订单中已处理"
                     f"（状态 {known[-1].status}），不能重复归还")
        # 错还拦截：设备隔离，绝不进入可用库存
        wrong_asset = (db.query(Asset)
                       .filter(Asset.serial == body.serial_scanned).first())
        held = False
        if wrong_asset is not None:
            wrong_asset.status = ASSET_QUARANTINED
            held = True
        db.add(ReturnRecord(
            order_id=order.id, order_item_id=None, employee_id=body.employee_id,
            serial_scanned=body.serial_scanned, result=RET_WRONG,
            photos=body.photos,
            condition_notes=body.notes or "错还拦截：序列号与出库记录不符"))
        db.commit()
        raise HTTPException(
            409, f"错还拦截：{body.serial_scanned} 与本订单任何在租件的"
                 f"出库序列号不符，归还被拒绝"
                 + ("，该设备已隔离锁定，不进入可用库存" if held else "（未知序列号）"))

    # 序列号匹配：按状态登记
    if body.condition == "hidden_damage":
        # 押金争议未决 → 先冻结对应资金（先校验再落库）
        remaining = (order.deposit_amount - order.frozen_amount
                     - order.deducted_amount - order.released_amount)
        freeze = body.freeze_amount if body.freeze_amount is not None else remaining
        if freeze <= 0 or freeze > remaining:
            raise HTTPException(400, f"冻结金额须为 (0, {remaining}]")

        record = ReturnRecord(
            order_id=order.id, order_item_id=item.id,
            employee_id=body.employee_id, serial_scanned=body.serial_scanned,
            result=RET_DAMAGE, photos=body.photos,
            condition_notes=body.notes or body.damage_description)
        db.add(record)
        db.flush()
        item.status = ITEM_DISPUTED
        item.asset.status = ASSET_QUARANTINED  # 暗伤设备隔离待复检

        dispute = Dispute(
            order_item_id=item.id, return_record_id=record.id,
            description=body.damage_description or body.notes,
            freeze_amount=freeze)
        db.add(dispute)
        db.flush()
        db.add(DepositTransaction(
            order_id=order.id, type="freeze", amount=freeze,
            reason=f"暗伤争议 #{dispute.id} 冻结押金",
            idempotency_key=f"freeze-dispute-{dispute.id}"))
        order.frozen_amount += freeze
        db.commit()
        return order_dict(order)

    if body.condition != "intact":
        raise HTTPException(400, "condition 只能是 intact 或 hidden_damage")

    # 完好件独立归还：不受其他件争议影响，直接回到可用库存
    db.add(ReturnRecord(
        order_id=order.id, order_item_id=item.id, employee_id=body.employee_id,
        serial_scanned=body.serial_scanned, result=RET_INTACT,
        photos=body.photos, condition_notes=body.notes))
    item.status = ITEM_RETURNED
    item.asset.status = ASSET_AVAILABLE
    db.commit()
    return order_dict(order)


@router.post("/items/{item_id}/swap")
def swap_item(order_id: int, item_id: int, body: SwapIn,
              db: Session = Depends(get_db)):
    """套装临时换件：旧件归还，新件必须重新办理完整出库交接。"""
    order = _get_order(db, order_id)
    _check_employee(db, body.employee_id)
    _validate_handover_evidence(body.photos, body.function_tests)

    old = db.get(OrderItem, item_id)
    if not old or old.order_id != order.id:
        raise HTTPException(404, "订单项不存在")
    if old.status != ITEM_OUT:
        raise HTTPException(409, f"该件当前状态为 {old.status}，只有在租件可换")

    new_asset = db.query(Asset).filter(Asset.serial == body.new_serial).first()
    if not new_asset:
        raise HTTPException(404, f"资产不存在: {body.new_serial}")
    if new_asset.status != ASSET_AVAILABLE:
        raise HTTPException(409, f"{body.new_serial} 当前状态为 "
                                 f"{new_asset.status}，不可用于换件")

    # 旧件：换件归还留痕
    db.add(ReturnRecord(
        order_id=order.id, order_item_id=old.id, employee_id=body.employee_id,
        serial_scanned=old.asset.serial, result=RET_INTACT,
        photos=[], condition_notes=f"换件归还，替换为 {body.new_serial}"))
    old.status = ITEM_SWAPPED
    old.asset.status = ASSET_AVAILABLE

    # 新件：同一槽位，指回旧件，重新办理出库交接（照片+功能测试）
    new_item = OrderItem(order_id=order.id, asset_id=new_asset.id,
                         slot=old.slot, replaces_item_id=old.id,
                         status=ITEM_OUT)
    db.add(new_item)
    db.flush()
    db.add(CheckoutRecord(
        order_item_id=new_item.id, employee_id=body.employee_id,
        serial_scanned=body.new_serial, photos=body.photos,
        function_tests=body.function_tests,
        notes=body.notes or f"换件出库，替换 {old.asset.serial}"))
    new_asset.status = ASSET_RENTED
    db.commit()
    db.refresh(new_item)
    return {"order": order_dict(order), "new_item": item_dict(new_item)}
