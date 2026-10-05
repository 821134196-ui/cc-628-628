"""数据模型。

核心设计：
- 订单不记"一套设备"，而是逐件 OrderItem（绑定具体序列号资产），并带 slot 槽位，
  换件时新 OrderItem 复用同一 slot 并通过 replaces_item_id 指回旧件，证据链不断。
- 设备收回（ReturnRecord）与押金结算（Settlement / DepositTransaction）分开留痕。
- DepositTransaction.idempotency_key 唯一，重复扣款请求不会二次扣减。
"""
from datetime import datetime

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey,
                        Integer, String, Text)
from sqlalchemy.orm import relationship

from .database import Base

# 资产状态
ASSET_AVAILABLE = "available"      # 可用库存
ASSET_RESERVED = "reserved"        # 已被订单占用（待出库）
ASSET_RENTED = "rented"            # 在租
ASSET_QUARANTINED = "quarantined"  # 隔离（错还锁定 / 待复检暗伤），不得进入可用库存
ASSET_MAINTENANCE = "maintenance"  # 维修中（暗伤定损后）

# 订单项状态
ITEM_PENDING = "pending"    # 待出库
ITEM_OUT = "out"            # 在租
ITEM_RETURNED = "returned"  # 已归还（含争议已结案）
ITEM_SWAPPED = "swapped"    # 已被换件替换
ITEM_DISPUTED = "disputed"  # 暗伤争议中

# 归还结论
RET_INTACT = "matched_intact"        # 序列号匹配且完好
RET_DAMAGE = "matched_damage"        # 序列号匹配但发现暗伤
RET_WRONG = "wrong_serial"           # 错还：序列号对不上，拦截

# 争议状态
DISPUTE_OPEN = "open"            # 待复检
DISPUTE_REINSPECTED = "reinspected"  # 复检确认损伤，待结算
DISPUTE_SETTLED = "settled"      # 已结算
DISPUTE_REJECTED = "rejected"    # 复检驳回（无损伤）


class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    phone = Column(String(32), default="")


class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True)
    serial = Column(String(64), unique=True, nullable=False, index=True)
    category = Column(String(32), nullable=False)  # body / lens / accessory
    model = Column(String(128), nullable=False)
    status = Column(String(32), default=ASSET_AVAILABLE, nullable=False)


class RentalOrder(Base):
    __tablename__ = "rental_orders"
    id = Column(Integer, primary_key=True)
    customer_id = Column(ForeignKey("customers.id"), nullable=False)
    status = Column(String(32), default="open", nullable=False)  # open / closed
    deposit_amount = Column(Float, default=0.0, nullable=False)
    # 押金台账（冗余汇总，流水以 DepositTransaction 为准）
    frozen_amount = Column(Float, default=0.0, nullable=False)
    deducted_amount = Column(Float, default=0.0, nullable=False)
    released_amount = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer")
    items = relationship("OrderItem", back_populates="order",
                         foreign_keys="OrderItem.order_id")


class OrderItem(Base):
    """订单中的一件设备（按序列号）。换件产生新行，slot 不变。"""
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(ForeignKey("rental_orders.id"), nullable=False)
    asset_id = Column(ForeignKey("assets.id"), nullable=False)
    slot = Column(Integer, nullable=False)  # 套装内槽位，换件后保持不变
    replaces_item_id = Column(ForeignKey("order_items.id"), nullable=True)
    status = Column(String(32), default=ITEM_PENDING, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("RentalOrder", back_populates="items",
                         foreign_keys=[order_id])
    asset = relationship("Asset")
    checkout = relationship("CheckoutRecord", uselist=False,
                            back_populates="order_item")
    returns = relationship("ReturnRecord", back_populates="order_item")
    dispute = relationship("Dispute", uselist=False, back_populates="order_item")


class CheckoutRecord(Base):
    """出库交接记录：逐件序列号核对 + 外观照片 + 对焦等功能测试。"""
    __tablename__ = "checkout_records"
    id = Column(Integer, primary_key=True)
    order_item_id = Column(ForeignKey("order_items.id"), unique=True,
                           nullable=False)
    employee_id = Column(ForeignKey("employees.id"), nullable=False)
    serial_scanned = Column(String(64), nullable=False)
    photos = Column(JSON, default=list)        # ["/uploads/xxx.jpg", ...]
    function_tests = Column(JSON, default=dict)  # {"focus": true, "aperture": true, ...}
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    order_item = relationship("OrderItem", back_populates="checkout")
    employee = relationship("Employee")


class ReturnRecord(Base):
    """归还/收回记录（设备侧，与押金结算分开留痕）。"""
    __tablename__ = "return_records"
    id = Column(Integer, primary_key=True)
    order_id = Column(ForeignKey("rental_orders.id"), nullable=False)
    order_item_id = Column(ForeignKey("order_items.id"), nullable=True)  # 错还时为空
    employee_id = Column(ForeignKey("employees.id"), nullable=False)
    serial_scanned = Column(String(64), nullable=False)
    result = Column(String(32), nullable=False)  # RET_INTACT / RET_DAMAGE / RET_WRONG
    photos = Column(JSON, default=list)
    condition_notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    order_item = relationship("OrderItem", back_populates="returns")
    employee = relationship("Employee")


class Dispute(Base):
    """暗伤争议：押金争议未决期间对应资金保持冻结。"""
    __tablename__ = "disputes"
    id = Column(Integer, primary_key=True)
    order_item_id = Column(ForeignKey("order_items.id"), unique=True,
                           nullable=False)
    return_record_id = Column(ForeignKey("return_records.id"), nullable=False)
    description = Column(Text, default="")
    status = Column(String(32), default=DISPUTE_OPEN, nullable=False)
    freeze_amount = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    order_item = relationship("OrderItem", back_populates="dispute")
    return_record = relationship("ReturnRecord")
    reinspection = relationship("ReInspection", uselist=False,
                                back_populates="dispute")
    settlement = relationship("Settlement", uselist=False,
                              back_populates="dispute")


class ReInspection(Base):
    """复检：复检人必须不是该件原出库员工（独立复检）。"""
    __tablename__ = "reinspections"
    id = Column(Integer, primary_key=True)
    dispute_id = Column(ForeignKey("disputes.id"), unique=True, nullable=False)
    inspector_id = Column(ForeignKey("employees.id"), nullable=False)
    damage_confirmed = Column(Boolean, nullable=False)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    dispute = relationship("Dispute", back_populates="reinspection")
    inspector = relationship("Employee")


class Settlement(Base):
    """押金结算记录（资金侧）。idempotency_key 保证重复请求不重复扣款。"""
    __tablename__ = "settlements"
    id = Column(Integer, primary_key=True)
    dispute_id = Column(ForeignKey("disputes.id"), unique=True, nullable=False)
    idempotency_key = Column(String(128), unique=True, nullable=False)
    items = Column(JSON, default=list)  # [{"reason": "镜片划伤", "amount": 800}, ...]
    total = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    dispute = relationship("Dispute", back_populates="settlement")


class DepositTransaction(Base):
    """押金流水：freeze / deduct / release。idempotency_key 唯一防重。"""
    __tablename__ = "deposit_transactions"
    id = Column(Integer, primary_key=True)
    order_id = Column(ForeignKey("rental_orders.id"), nullable=False)
    type = Column(String(16), nullable=False)  # freeze / deduct / release
    amount = Column(Float, nullable=False)
    reason = Column(String(256), default="")
    idempotency_key = Column(String(128), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
