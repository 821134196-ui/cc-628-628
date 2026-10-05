"""领域模型：设备序列号、订单交接、逐件检查、暗伤争议、押金账本。"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from .database import Base


# ---------- 枚举（字符串常量，SQLite 友好） ----------
class AssetType:
    CAMERA = "camera"   # 机身
    LENS = "lens"       # 镜头


class AssetStatus:
    AVAILABLE = "available"      # 可租
    RENTED = "rented"            # 在租
    DISPUTED = "disputed"        # 暗伤争议冻结中
    DAMAGED = "damaged"          # 复检确认损坏，不可出租
    WRONG_HELD = "wrong_held"    # 错还设备暂扣，绝不进入可用库存


class OrderStatus:
    ACTIVE = "active"            # 尚有设备未收回
    RETURNED = "returned"        # 设备全部收回（押金可能仍在争议）
    SETTLED = "settled"          # 押金已结清


class LineStatus:
    RENTED = "rented"                # 在租
    RETURNED_OK = "returned_ok"      # 完好归还
    RETURNED_DAMAGED = "damaged"     # 确认暗伤
    DISPUTED = "disputed"            # 发现暗伤待复检
    REPLACED = "replaced"            # 临时换件，原部件已收回结转到交接历史


class CheckType:
    CHECKOUT = "checkout"
    RETURN = "return"
    RECHECK = "recheck"


class CheckResult:
    PASS = "pass"
    FAIL = "fail"


class DisputeStatus:
    OPEN = "open"            # 待复检
    CONFIRMED = "confirmed"  # 复检确认暗伤
    REJECTED = "rejected"    # 复检驳回
    SETTLED = "settled"      # 扣款已结清


class TxnType:
    PAYMENT = "payment"      # 模拟支付通道：押金入账
    FREEZE = "freeze"        # 冻结
    UNFREEZE = "unfreeze"    # 解除冻结
    DEDUCT = "deduct"        # 扣减
    REFUND = "refund"        # 退还


# ---------- 员工 ----------
class Staff(Base):
    __tablename__ = "staff"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    role = Column(String(32), default="staff")  # staff / manager
    active = Column(Integer, default=1)


# ---------- 设备（按序列号逐件管理） ----------
class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True)
    serial_number = Column(String(64), unique=True, nullable=False, index=True)
    asset_type = Column(String(16), nullable=False)  # camera / lens
    brand = Column(String(64))
    model = Column(String(128))
    status = Column(String(16), default=AssetStatus.AVAILABLE, nullable=False)


# ---------- 订单 ----------
class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    order_no = Column(String(32), unique=True, nullable=False)
    customer_name = Column(String(64), nullable=False)
    customer_phone = Column(String(32))
    deposit_required = Column(Float, default=0.0)   # 应付押金
    status = Column(String(16), default=OrderStatus.ACTIVE, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    lines = relationship("OrderLine", back_populates="order", cascade="all, delete-orphan")
    checkout_events = relationship("CheckoutEvent", back_populates="order",
                                   cascade="all, delete-orphan")
    return_events = relationship("ReturnEvent", back_populates="order",
                                 cascade="all, delete-orphan")
    txns = relationship("DepositTxn", back_populates="order",
                        cascade="all, delete-orphan")
    disputes = relationship("DefectDispute", back_populates="order")


# ---------- 交接单（出库/换件每次重新办理，序号递增） ----------
class CheckoutEvent(Base):
    __tablename__ = "checkout_events"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    seq = Column(Integer, nullable=False)          # 第几次交接：1=首次出库，2+=换件
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="checkout_events")
    staff = relationship("Staff")
    lines = relationship("OrderLine", back_populates="checkout_event")


class ReturnEvent(Base):
    """归还交接记录，与押金结算分开留存。"""
    __tablename__ = "return_events"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="return_events")
    staff = relationship("Staff")
    checks = relationship("CheckRecord", back_populates="return_event")


# ---------- 订单借出部件行（每个序列号一行） ----------
class OrderLine(Base):
    __tablename__ = "order_lines"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    checkout_event_id = Column(Integer, ForeignKey("checkout_events.id"), nullable=False)
    status = Column(String(16), default=LineStatus.RENTED, nullable=False)
    replaced_by_line_id = Column(Integer, ForeignKey("order_lines.id"), nullable=True)

    order = relationship("Order", back_populates="lines")
    asset = relationship("Asset")
    checkout_event = relationship("CheckoutEvent", back_populates="lines")
    checks = relationship("CheckRecord", back_populates="line",
                          cascade="all, delete-orphan")
    replaced_by = relationship("OrderLine", remote_side=[id], uselist=False,
                               foreign_keys=[replaced_by_line_id])

    __table_args__ = (
        Index("ix_line_active", "order_id", "asset_id", "status"),
    )


# ---------- 检查记录与照片 ----------
class CheckRecord(Base):
    __tablename__ = "check_records"
    id = Column(Integer, primary_key=True)
    line_id = Column(Integer, ForeignKey("order_lines.id"), nullable=True)  # 复检/错还可能无行
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    check_type = Column(String(16), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    return_event_id = Column(Integer, ForeignKey("return_events.id"), nullable=True)
    result = Column(String(8), nullable=False)          # pass / fail
    focus_test = Column(String(32), default="untested") # 对焦功能测试
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    line = relationship("OrderLine", back_populates="checks")
    asset = relationship("Asset")
    staff = relationship("Staff")
    return_event = relationship("ReturnEvent", back_populates="checks")
    photos = relationship("Photo", back_populates="check", cascade="all, delete-orphan")


class Photo(Base):
    __tablename__ = "photos"
    id = Column(Integer, primary_key=True)
    check_id = Column(Integer, ForeignKey("check_records.id"), nullable=False)
    path = Column(String(256), nullable=False)          # 本地保存路径
    label = Column(String(64), default="")
    check = relationship("CheckRecord", back_populates="photos")


# ---------- 错还暂扣 ----------
class WrongReturn(Base):
    __tablename__ = "wrong_returns"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    scanned_sn = Column(String(64), nullable=False)
    matched_asset_id = Column(Integer, ForeignKey("assets.id"), nullable=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    note = Column(Text, default="")
    held = Column(Integer, default=1)                    # 仍在暂扣
    created_at = Column(DateTime, default=datetime.utcnow)

    matched_asset = relationship("Asset")
    staff = relationship("Staff")


# ---------- 暗伤争议与复检 ----------
class DefectDispute(Base):
    __tablename__ = "defect_disputes"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    line_id = Column(Integer, ForeignKey("order_lines.id"), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    return_check_id = Column(Integer, ForeignKey("check_records.id"), nullable=False)
    reporter_staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    status = Column(String(16), default=DisputeStatus.OPEN, nullable=False)
    description = Column(Text, default="")
    hold_amount = Column(Float, default=0.0)            # 争议未决时冻结的资金
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    order = relationship("Order", back_populates="disputes")
    line = relationship("OrderLine")
    asset = relationship("Asset")
    return_check = relationship("CheckRecord", foreign_keys=[return_check_id])
    reporter = relationship("Staff", foreign_keys=[reporter_staff_id])
    rechecks = relationship("Recheck", back_populates="dispute",
                            cascade="all, delete-orphan")
    quotes = relationship("DeductionQuote", back_populates="dispute",
                          cascade="all, delete-orphan")


class Recheck(Base):
    __tablename__ = "rechecks"
    id = Column(Integer, primary_key=True)
    dispute_id = Column(Integer, ForeignKey("defect_disputes.id"), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=False)
    result = Column(String(16), nullable=False)         # confirmed / rejected
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    dispute = relationship("DefectDispute", back_populates="rechecks")
    staff = relationship("Staff")


class DeductionQuote(Base):
    """报价与扣减原因，逐项展示。"""
    __tablename__ = "deduction_quotes"
    id = Column(Integer, primary_key=True)
    dispute_id = Column(Integer, ForeignKey("defect_disputes.id"), nullable=False)
    reason = Column(String(256), nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    dispute = relationship("DefectDispute", back_populates="quotes")


# ---------- 模拟押金支付 ----------
class DepositPayment(Base):
    __tablename__ = "deposit_payments"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    amount = Column(Float, nullable=False)
    gateway_ref = Column(String(64), nullable=False)    # 模拟支付通道流水号
    status = Column(String(16), default="succeeded")
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- 押金账本（收回设备与押金结算分别留记录） ----------
class DepositTxn(Base):
    __tablename__ = "deposit_txn"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    dispute_id = Column(Integer, ForeignKey("defect_disputes.id"), nullable=True)
    txn_type = Column(String(16), nullable=False)
    amount = Column(Float, nullable=False)
    reason = Column(Text, default="")
    idempotency_key = Column(String(64), nullable=True)
    staff_id = Column(Integer, ForeignKey("staff.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("Order", back_populates="txns")
    staff = relationship("Staff")

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_idempotency_key"),
        # 每个争议最多一笔扣款：重复扣款请求在业务层直接拦截
        Index("ix_deduct_dispute", "dispute_id", "txn_type"),
    )
