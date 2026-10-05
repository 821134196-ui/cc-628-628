"""请求体模型。"""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class OrderCreate(BaseModel):
    customer_id: int
    deposit_amount: float = Field(ge=0)
    serials: List[str] = Field(min_length=1)


class CheckoutIn(BaseModel):
    item_id: int
    employee_id: int
    serial_scanned: str
    photos: List[str] = Field(min_length=1)  # 至少一张外观照片
    function_tests: Dict[str, bool]          # 至少包含 focus（对焦）测试
    notes: str = ""


class ReturnIn(BaseModel):
    employee_id: int
    serial_scanned: str
    condition: str = "intact"  # intact / hidden_damage
    photos: List[str] = []
    notes: str = ""
    damage_description: str = ""
    freeze_amount: Optional[float] = None  # 暗伤时冻结金额，默认剩余全部


class SwapIn(BaseModel):
    employee_id: int
    new_serial: str
    photos: List[str] = Field(min_length=1)  # 换件必须重新办理交接：照片+功能测试
    function_tests: Dict[str, bool]
    notes: str = ""


class ReinspectIn(BaseModel):
    inspector_id: int
    damage_confirmed: bool
    notes: str = ""


class DeductionItem(BaseModel):
    reason: str
    amount: float = Field(ge=0)


class SettleIn(BaseModel):
    items: List[DeductionItem] = Field(min_length=1)  # 扣减原因逐项展示
    idempotency_key: str
