"""请求/响应结构。"""
from typing import List, Optional
from pydantic import BaseModel, Field


class StaffOut(BaseModel):
    id: int
    name: str
    role: str

    class Config:
        from_attributes = True


class AssetIn(BaseModel):
    serial_number: str
    asset_type: str  # camera / lens
    brand: Optional[str] = ""
    model: Optional[str] = ""


class AssetOut(AssetIn):
    id: int
    status: str

    class Config:
        from_attributes = True


class CheckoutItem(BaseModel):
    serial_number: str
    focus_test: str = "normal"   # normal / slow / fail
    notes: str = ""


class OrderCreate(BaseModel):
    customer_name: str
    customer_phone: str = ""
    deposit_required: float = 0.0
    staff_id: int
    items: List[CheckoutItem]
    note: str = ""


class ReplacementIn(BaseModel):
    staff_id: int
    out_serial: str               # 客户交回的旧件
    in_serial: str                # 门店临时换给客户的新件
    out_focus_test: str = "normal"
    in_focus_test: str = "normal"
    out_result: str = "pass"      # 旧件回收检查
    note: str = ""


class ReturnItem(BaseModel):
    serial_number: str
    result: str = "pass"          # pass / fail
    focus_test: str = "normal"
    notes: str = ""
    hold_amount: Optional[float] = 0.0  # 发现暗伤时申请冻结的押金金额


class ReturnIn(BaseModel):
    staff_id: int
    items: List[ReturnItem]
    note: str = ""


class RecheckIn(BaseModel):
    staff_id: int
    result: str                   # confirmed / rejected
    notes: str = ""


class QuoteIn(BaseModel):
    reason: str
    amount: float = Field(ge=0)


class DeductIn(BaseModel):
    staff_id: int
    idempotency_key: Optional[str] = None


class PayIn(BaseModel):
    amount: Optional[float] = None
    gateway_ref: Optional[str] = None
