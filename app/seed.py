"""初始化 SQLite 与演示种子数据（幂等：已有数据则跳过）。"""
from .database import Base, engine, SessionLocal
from . import models

Base.metadata.create_all(bind=engine)


def seed():
    db = SessionLocal()
    try:
        if db.query(models.Staff).count() > 0:
            print("数据库已有数据，跳过种子初始化。")
            return

        s1 = models.Staff(name="王出库", role="staff")
        s2 = models.Staff(name="李复检", role="staff")
        s3 = models.Staff(name="赵店长", role="manager")
        db.add_all([s1, s2, s3])
        db.flush()

        assets = [
            ("SN-A7R4-001", "camera", "Sony", "A7R IV"),
            ("SN-A7R4-002", "camera", "Sony", "A7R IV"),
            ("SN-CAN-R5-01", "camera", "Canon", "EOS R5"),
            ("SN-L2470-101", "lens", "Sony", "FE 24-70 F2.8 GM"),
            ("SN-L2470-102", "lens", "Sony", "FE 24-70 F2.8 GM"),
            ("SN-L7020-011", "lens", "Sony", "FE 70-200 F2.8 GM II"),
            ("SN-L3514-021", "lens", "Sony", "FE 35 F1.4 GM"),
            ("SN-L8514-031", "lens", "Sony", "FE 85 F1.4 GM"),
        ]
        for sn, t, brand, model in assets:
            db.add(models.Asset(serial_number=sn, asset_type=t,
                                brand=brand, model=model))
        db.commit()
        print(f"已初始化：3 名员工、{len(assets)} 件按序列号管理的设备。")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
