"""基础数据：员工、客户、资产、照片上传、争议列表。"""
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import UPLOAD_DIR
from ..database import get_db
from ..models import Asset, Customer, Dispute, Employee
from ..serialize import asset_dict, dispute_dict, employee_dict

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/employees")
def list_employees(db: Session = Depends(get_db)):
    return [employee_dict(e) for e in db.query(Employee).order_by(Employee.id)]


@router.get("/customers")
def list_customers(db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "phone": c.phone}
            for c in db.query(Customer).order_by(Customer.id)]


@router.get("/assets")
def list_assets(db: Session = Depends(get_db)):
    return [asset_dict(a) for a in db.query(Asset).order_by(Asset.id)]


@router.get("/disputes")
def list_disputes(db: Session = Depends(get_db)):
    out = []
    for d in db.query(Dispute).order_by(Dispute.id.desc()):
        row = dispute_dict(d)
        it = d.order_item
        row["order_id"] = it.order_id
        row["serial"] = it.asset.serial
        row["model"] = it.asset.model
        row["checkout_employee_id"] = it.checkout.employee_id if it.checkout else None
        out.append(row)
    return out


ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


@router.post("/uploads")
async def upload_photo(file: UploadFile):
    """照片本地保存到 uploads/，返回可访问路径。"""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件类型: {ext}")
    name = f"{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(await file.read())
    return {"path": f"/uploads/{name}"}
