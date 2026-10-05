"""演示数据：员工、客户、资产。幂等——已有数据则跳过。"""
from .database import SessionLocal
from .models import Asset, Customer, Employee

EMPLOYEES = ["张伟", "李娜", "王强"]
CUSTOMERS = [("陈晨", "13800000001"), ("刘洋", "13800000002")]
ASSETS = [
    ("SN-BODY-A7M4-001", "body", "Sony A7M4 机身"),
    ("SN-LENS-2470-001", "lens", "FE 24-70mm F2.8 GM 镜头"),
    ("SN-LENS-70200-001", "lens", "FE 70-200mm F4 G 镜头"),
    ("SN-LENS-50-001", "lens", "FE 50mm F1.8 镜头"),
    ("SN-LENS-2470-009", "lens", "FE 24-70mm F2.8 GM 镜头（另一支）"),
    ("SN-ACC-BAT-001", "accessory", "NP-FZ100 电池"),
]


def seed_if_empty():
    db = SessionLocal()
    try:
        if db.query(Employee).count() == 0:
            for name in EMPLOYEES:
                db.add(Employee(name=name))
        if db.query(Customer).count() == 0:
            for name, phone in CUSTOMERS:
                db.add(Customer(name=name, phone=phone))
        if db.query(Asset).count() == 0:
            for serial, category, model in ASSETS:
                db.add(Asset(serial=serial, category=category, model=model))
        db.commit()
    finally:
        db.close()
