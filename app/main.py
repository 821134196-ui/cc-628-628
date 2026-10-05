"""FastAPI 入口：设备资产、逐序列号交接、归还拦截、换件、暗伤复检、押金账本。"""
import os
import shutil
import uuid
from datetime import datetime
from typing import List

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from . import models, schemas, deposit
from .database import Base, engine, get_db, UPLOAD_DIR

Base.metadata.create_all(bind=engine)

app = FastAPI(title="相机镜头租赁序列号核对与暗伤争议系统")

TERMINAL_LINE = {models.LineStatus.RETURNED_OK, models.LineStatus.RETURNED_DAMAGED,
                 models.LineStatus.REPLACED}


# ================= 序列化 =================
def staff_brief(s):
    return {"id": s.id, "name": s.name, "role": s.role} if s else None


def photo_dict(p: models.Photo):
    return {"id": p.id, "url": f"/static/uploads/{os.path.basename(p.path)}",
            "label": p.label}


def check_dict(c: models.CheckRecord):
    return {
        "id": c.id,
        "check_type": c.check_type,
        "result": c.result,
        "focus_test": c.focus_test,
        "notes": c.notes,
        "staff": staff_brief(c.staff),
        "created_at": c.created_at.isoformat(timespec="seconds"),
        "photos": [photo_dict(p) for p in c.photos],
    }


def line_dict(ln: models.OrderLine):
    checkout_checks = [c for c in ln.checks if c.check_type == models.CheckType.CHECKOUT]
    return_checks = [c for c in ln.checks
                     if c.check_type in (models.CheckType.RETURN, models.CheckType.RECHECK)]
    return {
        "id": ln.id,
        "asset": {"id": ln.asset.id, "serial_number": ln.asset.serial_number,
                  "asset_type": ln.asset.asset_type, "brand": ln.asset.brand,
                  "model": ln.asset.model, "status": ln.asset.status},
        "status": ln.status,
        "checkout_event_seq": ln.checkout_event.seq,
        "checkout_staff": staff_brief(ln.checkout_event.staff),
        "checkout_at": ln.checkout_event.created_at.isoformat(timespec="seconds"),
        "replaced_by_line_id": ln.replaced_by_line_id,
        "checkout_checks": [check_dict(c) for c in checkout_checks],
        "return_checks": [check_dict(c) for c in return_checks],
    }


def dispute_dict(d: models.DefectDispute):
    return {
        "id": d.id,
        "line_id": d.line_id,
        "serial_number": d.asset.serial_number,
        "status": d.status,
        "description": d.description,
        "hold_amount": d.hold_amount,
        "reporter": staff_brief(d.reporter),
        "checkout_staff": staff_brief(d.line.checkout_event.staff),
        "created_at": d.created_at.isoformat(timespec="seconds"),
        "rechecks": [{"id": r.id, "staff": staff_brief(r.staff), "result": r.result,
                      "notes": r.notes,
                      "created_at": r.created_at.isoformat(timespec="seconds")}
                     for r in d.rechecks],
        "quotes": [{"id": q.id, "reason": q.reason, "amount": q.amount}
                   for q in d.quotes],
    }


def order_dict(db: Session, o: models.Order, detail: bool = False):
    data = {
        "id": o.id,
        "order_no": o.order_no,
        "customer_name": o.customer_name,
        "customer_phone": o.customer_phone,
        "deposit_required": o.deposit_required,
        "status": o.status,
        "created_at": o.created_at.isoformat(timespec="seconds"),
        "balances": deposit.balances(db, o.id),
        "line_count": len(o.lines),
    }
    if detail:
        data["lines"] = [line_dict(ln) for ln in o.lines]
        data["checkout_events"] = [{
            "id": e.id, "seq": e.seq, "staff": staff_brief(e.staff),
            "note": e.note, "created_at": e.created_at.isoformat(timespec="seconds"),
        } for e in sorted(o.checkout_events, key=lambda e: e.seq)]
        data["return_events"] = [{
            "id": e.id, "staff": staff_brief(e.staff), "note": e.note,
            "created_at": e.created_at.isoformat(timespec="seconds"),
        } for e in o.return_events]
        data["disputes"] = [dispute_dict(d) for d in o.disputes]
        data["ledger"] = [{
            "id": t.id, "txn_type": t.txn_type, "amount": t.amount,
            "reason": t.reason, "staff": staff_brief(t.staff),
            "dispute_id": t.dispute_id,
            "created_at": t.created_at.isoformat(timespec="seconds"),
        } for t in sorted(o.txns, key=lambda t: t.id)]
    return data


def get_staff(db: Session, staff_id: int) -> models.Staff:
    s = db.get(models.Staff, staff_id)
    if not s or not s.active:
        raise HTTPException(400, f"员工 {staff_id} 不存在或已停用")
    return s


def recompute_order_status(db: Session, o: models.Order):
    if all(ln.status in TERMINAL_LINE for ln in o.lines):
        if o.status == models.OrderStatus.ACTIVE:
            o.status = models.OrderStatus.RETURNED


# ================= 基础数据 =================
@app.get("/api/staff", response_model=List[schemas.StaffOut])
def list_staff(db: Session = Depends(get_db)):
    return db.query(models.Staff).filter(models.Staff.active == 1).all()


@app.get("/api/assets")
def list_assets(status: str | None = None, db: Session = Depends(get_db)):
    q = db.query(models.Asset)
    if status:
        q = q.filter(models.Asset.status == status)
    return [{"id": a.id, "serial_number": a.serial_number, "asset_type": a.asset_type,
             "brand": a.brand, "model": a.model, "status": a.status}
            for a in q.order_by(models.Asset.asset_type, models.Asset.serial_number).all()]


@app.post("/api/assets", status_code=201)
def create_asset(body: schemas.AssetIn, db: Session = Depends(get_db)):
    if body.asset_type not in (models.AssetType.CAMERA, models.AssetType.LENS):
        raise HTTPException(400, "asset_type 必须是 camera 或 lens")
    sn = body.serial_number.strip()
    if not sn:
        raise HTTPException(400, "序列号不能为空")
    if db.query(models.Asset).filter_by(serial_number=sn).first():
        raise HTTPException(409, f"序列号 {sn} 已存在")
    a = models.Asset(serial_number=sn, asset_type=body.asset_type,
                     brand=body.brand or "", model=body.model or "")
    db.add(a)
    db.commit()
    db.refresh(a)
    return {"id": a.id, "serial_number": a.serial_number, "status": a.status}


@app.get("/api/wrong-returns")
def list_wrong_returns(held_only: int = 1, db: Session = Depends(get_db)):
    q = db.query(models.WrongReturn)
    if held_only:
        q = q.filter(models.WrongReturn.held == 1)
    out = []
    for w in q.order_by(models.WrongReturn.id.desc()).all():
        out.append({
            "id": w.id, "order_id": w.order_id, "scanned_sn": w.scanned_sn,
            "matched_asset": ({"id": w.matched_asset.id,
                               "serial_number": w.matched_asset.serial_number,
                               "status": w.matched_asset.status}
                              if w.matched_asset else None),
            "staff": staff_brief(w.staff), "note": w.note, "held": w.held,
            "created_at": w.created_at.isoformat(timespec="seconds"),
        })
    return out


@app.post("/api/wrong-returns/{wid}/release")
def release_wrong(wid: int, staff_id: int = Form(...), note: str = Form(""),
                  db: Session = Depends(get_db)):
    """错还设备核实去向后退暂扣（仍不会自动并入可用库存之外的订单）。"""
    get_staff(db, staff_id)
    w = db.get(models.WrongReturn, wid)
    if not w or not w.held:
        raise HTTPException(404, "暂扣记录不存在或已处理")
    w.held = 0
    a = w.matched_asset
    if a and a.status == models.AssetStatus.WRONG_HELD:
        rented_elsewhere = db.query(models.OrderLine).filter(
            models.OrderLine.asset_id == a.id,
            models.OrderLine.status == models.LineStatus.RENTED,
        ).first()
        a.status = (models.AssetStatus.RENTED if rented_elsewhere
                    else models.AssetStatus.AVAILABLE)
    db.commit()
    return {"ok": True}


# ================= 订单 / 出库 =================
@app.post("/api/orders", status_code=201)
def create_order(body: schemas.OrderCreate, db: Session = Depends(get_db)):
    staff = get_staff(db, body.staff_id)
    if not body.items:
        raise HTTPException(400, "出库至少登记一个部件序列号")
    sns = [it.serial_number.strip() for it in body.items]
    if len(set(sns)) != len(sns):
        raise HTTPException(400, "出库序列号重复")

    order = models.Order(
        order_no=f"RO-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}",
        customer_name=body.customer_name, customer_phone=body.customer_phone,
        deposit_required=body.deposit_required,
    )
    db.add(order)
    db.flush()
    event = models.CheckoutEvent(order_id=order.id, seq=1, staff_id=staff.id,
                                 note=body.note)
    db.add(event)
    db.flush()

    blocked = []
    for it in body.items:
        sn = it.serial_number.strip()
        a = db.query(models.Asset).filter_by(serial_number=sn).first()
        if not a:
            blocked.append({"serial_number": sn, "reason": "序列号不存在"})
            continue
        if a.status != models.AssetStatus.AVAILABLE:
            blocked.append({"serial_number": sn, "reason": f"设备不可用（{a.status}）",
                            "asset_status": a.status})
            continue
        a.status = models.AssetStatus.RENTED
        ln = models.OrderLine(order_id=order.id, asset_id=a.id,
                              checkout_event_id=event.id, status=models.LineStatus.RENTED)
        db.add(ln)
        db.flush()
        c = models.CheckRecord(line_id=ln.id, asset_id=a.id,
                               check_type=models.CheckType.CHECKOUT, staff_id=staff.id,
                               result=models.CheckResult.PASS, focus_test=it.focus_test,
                               notes=it.notes)
        db.add(c)

    if blocked:
        db.rollback()
        raise HTTPException(409, {"message": "出库被拦截，以下序列号不可用",
                                  "blocked": blocked})
    db.commit()
    db.refresh(order)
    return order_dict(db, order, detail=True)


@app.get("/api/orders")
def list_orders(db: Session = Depends(get_db)):
    return [order_dict(db, o) for o in
            db.query(models.Order).order_by(models.Order.id.desc()).all()]


@app.get("/api/orders/{oid}")
def get_order(oid: int, db: Session = Depends(get_db)):
    o = db.get(models.Order, oid)
    if not o:
        raise HTTPException(404, "订单不存在")
    return order_dict(db, o, detail=True)


# ================= 临时换件（重新办理交接） =================
@app.post("/api/orders/{oid}/replace", status_code=201)
def replace_item(oid: int, body: schemas.ReplacementIn, db: Session = Depends(get_db)):
    staff = get_staff(db, body.staff_id)
    o = db.get(models.Order, oid)
    if not o:
        raise HTTPException(404, "订单不存在")

    old_asset = db.query(models.Asset).filter_by(serial_number=body.out_serial).first()
    new_asset = db.query(models.Asset).filter_by(serial_number=body.in_serial).first()
    if not old_asset:
        raise HTTPException(404, f"交回部件 {body.out_serial} 序列号不存在")
    if not new_asset:
        raise HTTPException(404, f"换出部件 {body.in_serial} 序列号不存在")
    if old_asset.id == new_asset.id:
        raise HTTPException(400, "换件前后序列号不能相同")

    old_line = db.query(models.OrderLine).filter(
        models.OrderLine.order_id == oid,
        models.OrderLine.asset_id == old_asset.id,
        models.OrderLine.status == models.LineStatus.RENTED,
    ).first()
    if not old_line:
        raise HTTPException(409, f"{body.out_serial} 不是本订单当前在租部件，不能换件")
    if new_asset.status != models.AssetStatus.AVAILABLE:
        raise HTTPException(409, f"换出部件 {body.in_serial} 当前不可用（{new_asset.status}）")

    # 1) 旧件收回检查（独立于整套归还）
    old_result = (models.CheckResult.FAIL if body.out_result == "fail"
                  else models.CheckResult.PASS)
    old_check = models.CheckRecord(
        line_id=old_line.id, asset_id=old_asset.id,
        check_type=models.CheckType.RETURN, staff_id=staff.id,
        result=old_result, focus_test=body.out_focus_test,
        notes=f"换件收回：{body.note}")
    db.add(old_check)

    # 2) 重新办理交接：新的 CheckoutEvent（seq 递增），新序列号独立成行
    seq = max(e.seq for e in o.checkout_events) + 1
    event = models.CheckoutEvent(order_id=o.id, seq=seq, staff_id=staff.id,
                                 note=f"临时换件：{body.out_serial} → {body.in_serial}；{body.note}")
    db.add(event)
    db.flush()
    new_asset.status = models.AssetStatus.RENTED
    new_line = models.OrderLine(order_id=o.id, asset_id=new_asset.id,
                                checkout_event_id=event.id,
                                status=models.LineStatus.RENTED)
    db.add(new_line)
    db.flush()
    new_check = models.CheckRecord(
        line_id=new_line.id, asset_id=new_asset.id,
        check_type=models.CheckType.CHECKOUT, staff_id=staff.id,
        result=models.CheckResult.PASS, focus_test=body.in_focus_test,
        notes=f"换件出库：{body.note}")
    db.add(new_check)
    old_line.replaced_by_line_id = new_line.id

    dispute = None
    if old_result == models.CheckResult.FAIL:
        old_line.status = models.LineStatus.DISPUTED
        old_asset.status = models.AssetStatus.DISPUTED
        dispute = models.DefectDispute(
            order_id=o.id, line_id=old_line.id, asset_id=old_asset.id,
            return_check_id=old_check.id, reporter_staff_id=staff.id,
            description=f"换件收回时发现暗伤：{body.note}",
            hold_amount=max(float(o.deposit_required or 0.0), 0.0))
        db.add(dispute)
        db.flush()
        if dispute.hold_amount > 0:
            try:
                deposit.freeze_for_dispute(db, dispute, staff.id)
            except HTTPException:
                pass  # 押金未支付等情况：争议仍登记，结算前再拦截
    else:
        old_line.status = models.LineStatus.REPLACED
        old_asset.status = models.AssetStatus.AVAILABLE

    db.commit()
    return {"ok": True, "checkout_event_seq": seq,
            "old_line": line_dict(old_line), "new_line": line_dict(new_line),
            "dispute_id": dispute.id if dispute else None}


# ================= 归还（逐件核对，错还拦截） =================
@app.post("/api/orders/{oid}/return")
def return_items(oid: int, body: schemas.ReturnIn, db: Session = Depends(get_db)):
    staff = get_staff(db, body.staff_id)
    o = db.get(models.Order, oid)
    if not o:
        raise HTTPException(404, "订单不存在")
    if not body.items:
        raise HTTPException(400, "至少扫描一个序列号")

    event = models.ReturnEvent(order_id=o.id, staff_id=staff.id, note=body.note)
    db.add(event)
    db.flush()

    returned, wrong, dispute_ids = [], [], []
    for it in body.items:
        sn = it.serial_number.strip()
        asset = db.query(models.Asset).filter_by(serial_number=sn).first()

        # —— 错还拦截：序列号不属于本订单当前在租部件 ——
        line = None
        if asset:
            line = db.query(models.OrderLine).filter(
                models.OrderLine.order_id == oid,
                models.OrderLine.asset_id == asset.id,
                models.OrderLine.status == models.LineStatus.RENTED,
            ).first()
        if line is None:
            reason = "序列号不存在" if not asset else "该设备不属于本订单当前在租清单"
            wr = models.WrongReturn(order_id=oid, scanned_sn=sn,
                                    matched_asset_id=asset.id if asset else None,
                                    staff_id=staff.id,
                                    note=f"{reason}；{it.notes}")
            db.add(wr)
            # 错还设备一律暂扣，不能进入可用库存
            if asset and asset.status == models.AssetStatus.AVAILABLE:
                asset.status = models.AssetStatus.WRONG_HELD
            db.flush()
            wrong.append({"serial_number": sn, "reason": reason,
                          "wrong_return_id": wr.id,
                          "asset_status": asset.status if asset else None})
            continue

        result = (models.CheckResult.FAIL if it.result == "fail"
                  else models.CheckResult.PASS)
        c = models.CheckRecord(line_id=line.id, asset_id=asset.id,
                               check_type=models.CheckType.RETURN, staff_id=staff.id,
                               return_event_id=event.id, result=result,
                               focus_test=it.focus_test, notes=it.notes)
        db.add(c)
        db.flush()

        if result == models.CheckResult.PASS:
            line.status = models.LineStatus.RETURNED_OK
            asset.status = models.AssetStatus.AVAILABLE  # 该件独立归还，立即可再租
            returned.append({"serial_number": sn, "result": "pass",
                             "asset_status": asset.status})
        else:
            line.status = models.LineStatus.DISPUTED
            asset.status = models.AssetStatus.DISPUTED
            hold_amount = max(float(it.hold_amount or 0.0), 0.0)
            d = models.DefectDispute(
                order_id=oid, line_id=line.id, asset_id=asset.id,
                return_check_id=c.id, reporter_staff_id=staff.id,
                description=it.notes or "归还检查发现暗伤",
                hold_amount=hold_amount)
            db.add(d)
            db.flush()
            if hold_amount > 0:
                deposit.freeze_for_dispute(db, d, staff.id)
            dispute_ids.append(d.id)
            returned.append({"serial_number": sn, "result": "fail",
                             "dispute_id": d.id, "asset_status": asset.status})

    recompute_order_status(db, o)
    db.commit()
    # HTTP 200 中同时区分正常归还与被拦截的错还
    return {"return_event_id": event.id, "returned": returned,
            "wrong_returns": wrong, "dispute_ids": dispute_ids}


# ================= 暗伤复检（非原出库员工） =================
@app.post("/api/orders/{oid}/disputes/{did}/recheck")
def recheck(oid: int, did: int, body: schemas.RecheckIn, db: Session = Depends(get_db)):
    staff = get_staff(db, body.staff_id)
    d = db.get(models.DefectDispute, did)
    if not d or d.order_id != oid:
        raise HTTPException(404, "争议不存在")
    if d.status != models.DisputeStatus.OPEN:
        raise HTTPException(409, f"争议已结案（{d.status}），不能再次复检")

    original_staff_id = d.line.checkout_event.staff_id
    if staff.id == original_staff_id:
        raise HTTPException(
            403,
            f"独立复检被拒绝：{staff.name} 是该部件（{d.asset.serial_number}）的"
            f"原出库员工，必须由其他员工复检",
        )
    if body.result not in ("confirmed", "rejected"):
        raise HTTPException(400, "复检结果必须是 confirmed 或 rejected")

    rc = models.Recheck(dispute_id=d.id, staff_id=staff.id, result=body.result,
                        notes=body.notes)
    db.add(rc)
    c = models.CheckRecord(line_id=d.line_id, asset_id=d.asset_id,
                           check_type=models.CheckType.RECHECK, staff_id=staff.id,
                           result=(models.CheckResult.FAIL if body.result == "confirmed"
                                   else models.CheckResult.PASS),
                           notes=body.notes)
    db.add(c)
    d.status = (models.DisputeStatus.CONFIRMED if body.result == "confirmed"
                else models.DisputeStatus.REJECTED)
    d.resolved_at = datetime.utcnow()

    if body.result == "confirmed":
        d.asset.status = models.AssetStatus.DAMAGED
        d.line.status = models.LineStatus.RETURNED_DAMAGED
    else:
        deposit.release_freeze(db, d, staff.id)
        d.asset.status = models.AssetStatus.AVAILABLE
        d.line.status = models.LineStatus.RETURNED_OK

    recompute_order_status(db, d.order)
    db.commit()
    return {"ok": True, "dispute": dispute_dict(d)}


@app.post("/api/orders/{oid}/disputes/{did}/quotes", status_code=201)
def add_quote(oid: int, did: int, body: schemas.QuoteIn, db: Session = Depends(get_db)):
    d = db.get(models.DefectDispute, did)
    if not d or d.order_id != oid:
        raise HTTPException(404, "争议不存在")
    if d.status != models.DisputeStatus.CONFIRMED:
        raise HTTPException(409, "仅复检确认的暗伤可以登记报价")
    if not body.reason.strip():
        raise HTTPException(400, "扣减原因不能为空")
    q = models.DeductionQuote(dispute_id=d.id, reason=body.reason.strip(),
                              amount=round(body.amount, 2))
    db.add(q)
    db.commit()
    db.refresh(q)
    return {"id": q.id, "reason": q.reason, "amount": q.amount}


@app.post("/api/orders/{oid}/disputes/{did}/deduct")
def deduct(oid: int, did: int, body: schemas.DeductIn, db: Session = Depends(get_db)):
    get_staff(db, body.staff_id)
    d = db.get(models.DefectDispute, did)
    if not d or d.order_id != oid:
        raise HTTPException(404, "争议不存在")
    if d.status not in (models.DisputeStatus.CONFIRMED, models.DisputeStatus.SETTLED):
        raise HTTPException(409, "争议未复检确认，不能扣减")

    deduct_count_before = db.query(models.DepositTxn).filter(
        models.DepositTxn.dispute_id == d.id,
        models.DepositTxn.txn_type == models.TxnType.DEDUCT).count()

    txn = deposit.settle_dispute(db, d, body.staff_id, body.idempotency_key)
    d.status = models.DisputeStatus.SETTLED
    recompute_order_status(db, d.order)
    db.commit()

    deduct_count_after = db.query(models.DepositTxn).filter(
        models.DepositTxn.dispute_id == d.id,
        models.DepositTxn.txn_type == models.TxnType.DEDUCT).count()
    return {
        "ok": True,
        "deducted_now": deduct_count_after > deduct_count_before,
        "duplicate_request": deduct_count_after == deduct_count_before,
        "txn_id": txn.id,
        "amount": txn.amount,
        "note": ("重复扣款请求已拦截，未再次扣减，返回原扣款记录"
                 if deduct_count_after == deduct_count_before else "扣款成功"),
        "balances": deposit.balances(db, oid),
    }


# ================= 押金（模拟支付通道） =================
@app.post("/api/orders/{oid}/deposit/pay")
def pay(oid: int, body: schemas.PayIn, db: Session = Depends(get_db)):
    o = db.get(models.Order, oid)
    if not o:
        raise HTTPException(404, "订单不存在")
    deposit.pay_deposit(db, o, body.amount, body.gateway_ref)
    db.commit()
    return {"ok": True, "balances": deposit.balances(db, oid)}


@app.post("/api/orders/{oid}/deposit/refund")
def refund(oid: int, staff_id: int = Form(...), db: Session = Depends(get_db)):
    get_staff(db, staff_id)
    o = db.get(models.Order, oid)
    if not o:
        raise HTTPException(404, "订单不存在")
    open_d = [d for d in o.disputes if d.status == models.DisputeStatus.OPEN]
    if open_d:
        raise HTTPException(409, "尚有暗伤争议待复检，押金不能结清")
    if any(ln.status == models.LineStatus.RENTED for ln in o.lines):
        raise HTTPException(409, "仍有设备在租，不能结清押金")
    txn = deposit.refund_remaining(db, o, staff_id)
    o.status = models.OrderStatus.SETTLED
    db.commit()
    return {"ok": True, "refund_txn_id": txn.id if txn else None,
            "refunded": txn.amount if txn else 0.0,
            "balances": deposit.balances(db, oid)}


# ================= 照片（本地保存） =================
@app.post("/api/checks/{cid}/photos", status_code=201)
def upload_photo(cid: int, file: UploadFile = File(...),
                 label: str = Form(""), db: Session = Depends(get_db)):
    c = db.get(models.CheckRecord, cid)
    if not c:
        raise HTTPException(404, "检查记录不存在")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise HTTPException(400, "仅支持 jpg/png/webp/gif 图片")
    name = f"check{cid}_{uuid.uuid4().hex}{ext}"
    dest = os.path.join(UPLOAD_DIR, name)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    p = models.Photo(check_id=cid, path=dest, label=label)
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "url": f"/static/uploads/{name}", "label": p.label}


# ================= 静态前端 =================
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
