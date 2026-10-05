"""端到端验证（使用临时 SQLite，不影响正式数据）：

  1. 错还拦截：未知序列号 / 非本订单在租部件被拦截，设备不进入可用库存
  2. 完好部件独立归还：在争议未决、整套未齐时即可再租（供另一单换件）
  3. 独立复检权限：原出库员工复检 403，其他员工可复检
  4. 重复扣款：多次扣款请求只扣一次，返回原记录（幂等）
  5. 换件证据链：新序列号挂在新的交接批次与出库员工上，前后证据可分别追溯
  6. 照片本地保存
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="rental_verify_")
os.environ["RENTAL_DB"] = os.path.join(_tmp, "verify.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app import models  # noqa: E402

client = TestClient(app)

PASS_COUNT = 0
FAIL_COUNT = 0


def check(name, cond, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
        print(f"  ✅ {name}")
    else:
        FAIL_COUNT += 1
        print(f"  ❌ {name}  {detail}")


def must(r, code=200):
    assert r.status_code == code, f"期望 {code}，实际 {r.status_code}：{r.text}"
    return r.json()


# ---------- 夹具：员工 + 序列号设备 ----------
db = SessionLocal()
s1 = models.Staff(name="王出库", role="staff")
s2 = models.Staff(name="李复检", role="staff")
s3 = models.Staff(name="赵店长", role="manager")
db.add_all([s1, s2, s3])
db.commit()
db.refresh(s1); db.refresh(s2); db.refresh(s3)
db.close()

for sn, t, brand, model in [
    ("SN-C-001", "camera", "Sony", "A7R4"),
    ("SN-C-002", "camera", "Canon", "R5"),
    ("SN-L-001", "lens", "Sony", "24-70"),
    ("SN-L-002", "lens", "Sony", "70-200"),
    ("SN-X-100", "lens", "Nikon", "35/1.4"),  # 库存中的他人/未出租设备
]:
    must(client.post("/api/assets", json={
        "serial_number": sn, "asset_type": t, "brand": brand, "model": model}), 201)

print("\n=== 场景1：订单#1 按序列号逐件出库（机身 C-001 + 镜头 L-001，押金 5000）===")
o1 = must(client.post("/api/orders", json={
    "customer_name": "陈客户", "deposit_required": 5000, "staff_id": s1.id,
    "items": [
        {"serial_number": "SN-C-001", "focus_test": "normal", "notes": "出库外观完好"},
        {"serial_number": "SN-L-001", "focus_test": "normal"},
    ],
}), 201)
check("出库登记 2 个序列号部件", len(o1["lines"]) == 2)
check("两个部件均为在租状态",
      all(l["asset"]["status"] == "rented" for l in o1["lines"]))
cam_line = next(l for l in o1["lines"] if l["asset"]["serial_number"] == "SN-C-001")
checkout_check_id = cam_line["checkout_checks"][0]["id"]

pay = must(client.post(f"/api/orders/{o1['id']}/deposit/pay", json={}), 200)
check("模拟支付通道押金到账 5000", pay["balances"]["paid"] == 5000
      and pay["balances"]["available"] == 5000, pay["balances"])

print("\n=== 场景2：错还拦截 + 完好镜头独立归还（机身争议尚未发生）===")
r = must(client.post(f"/api/orders/{o1['id']}/return", json={
    "staff_id": s2.id,
    "items": [
        {"serial_number": "SN-GHOST-000", "notes": "库中不存在的序列号"},
        {"serial_number": "SN-X-100", "notes": "别家的镜头错还过来"},
        {"serial_number": "SN-L-001", "result": "pass", "notes": "镜头完好先还"},
    ],
}), 200)
wrong_sns = {w["serial_number"] for w in r["wrong_returns"]}
check("未知序列号被拦截", "SN-GHOST-000" in wrong_sns)
check("非本订单部件被拦截", "SN-X-100" in wrong_sns)
check("正常件 L-001 归还成功",
      any(x["serial_number"] == "SN-L-001" and x["result"] == "pass"
          for x in r["returned"]))

assets = {a["serial_number"]: a for a in must(client.get("/api/assets"))}
check("错还设备 SN-X-100 被暂扣，未进入可用库存",
      assets["SN-X-100"]["status"] == "wrong_held",
      assets["SN-X-100"]["status"])
available_sns = {a["serial_number"] for a in must(client.get("/api/assets?status=available"))}
check("可用库存中不含错还设备", "SN-X-100" not in available_sns)
check("完好归还的 L-001 独立进入可用库存", "SN-L-001" in available_sns)

held = must(client.get("/api/wrong-returns?held_only=1"))
check("暂扣区可查到 2 条错还记录", len(held) == 2, held)

o1_now = must(client.get(f"/api/orders/{o1['id']}"))
check("订单仍在租（机身未还），部件可独立归还不等待整套",
      o1_now["status"] == "active", o1_now["status"])
return_events = o1_now["return_events"]
check("收回设备独立留有归还批次记录", len(return_events) == 1)

print("\n=== 场景3：订单#2 出库 C-002 + L-002，并把 L-002 临时换成 L-001 ===")
o2 = must(client.post("/api/orders", json={
    "customer_name": "林客户", "deposit_required": 3000, "staff_id": s1.id,
    "items": [
        {"serial_number": "SN-C-002", "focus_test": "normal"},
        {"serial_number": "SN-L-002", "focus_test": "normal"},
    ],
}), 201)
must(client.post(f"/api/orders/{o2['id']}/deposit/pay", json={}), 200)

# 此时订单#1 的机身争议还未开始，L-001 已凭“独立归还”在订单#2 中重新出租
rp = must(client.post(f"/api/orders/{o2['id']}/replace", json={
    "staff_id": s1.id,
    "out_serial": "SN-L-002", "in_serial": "SN-L-001",
    "out_result": "pass", "in_focus_test": "normal",
    "note": "客户嫌 70-200 太重，换 24-70",
}), 201)
check("换件产生第 2 次交接批次", rp["checkout_event_seq"] == 2)
check("旧件 L-002 结转并恢复可租",
      rp["old_line"]["status"] == "replaced"
      and rp["old_line"]["asset"]["status"] == "available",
      rp["old_line"]["status"])
check("新件 L-001 在租并挂在第 2 次交接",
      rp["new_line"]["asset"]["serial_number"] == "SN-L-001"
      and rp["new_line"]["checkout_event_seq"] == 2
      and rp["new_line"]["checkout_staff"]["id"] == s1.id)

# 换件校验：把不属于本订单在租清单的设备拿来“换”
bad = client.post(f"/api/orders/{o2['id']}/replace", json={
    "staff_id": s1.id, "out_serial": "SN-X-100", "in_serial": "SN-L-002"})
check("非本订单在租件不能换件（409）", bad.status_code == 409, bad.text)

print("\n=== 场景4：订单#1 机身发现暗伤 → 冻结押金 → 独立复检权限 ===")
r = must(client.post(f"/api/orders/{o1['id']}/return", json={
    "staff_id": s2.id,
    "items": [{"serial_number": "SN-C-001", "result": "fail", "focus_test": "slow",
               "notes": "前组镜片边缘有划伤，出库照片没有", "hold_amount": 3000}],
}), 200)
d1_id = r["returned"][0]["dispute_id"]
o1d = must(client.get(f"/api/orders/{o1['id']}"))
b1 = o1d["balances"]
check("争议未决先冻结对应资金 3000",
      b1["frozen"] == 3000 and b1["available"] == 2000, b1)

# 原出库员工（王出库 = s1）复检 → 403
forbidden = client.post(f"/api/orders/{o1['id']}/disputes/{d1_id}/recheck", json={
    "staff_id": s1.id, "result": "confirmed", "notes": "我自己看看没问题吧"})
check("原出库员工复检被拒绝（403）",
      forbidden.status_code == 403 and "原出库员工" in forbidden.json()["detail"],
      forbidden.text)

# 非原出库员工（李复检 = s2）复检确认
ok = must(client.post(f"/api/orders/{o1['id']}/disputes/{d1_id}/recheck", json={
    "staff_id": s2.id, "result": "confirmed", "notes": "复检确认划伤属实，非出库既有"
}), 200)
check("其他员工复检成功：确认暗伤", ok["dispute"]["status"] == "confirmed")
assets = {a["serial_number"]: a for a in must(client.get("/api/assets"))}
check("确认暗伤后设备停用（damaged，不可出租）",
      assets["SN-C-001"]["status"] == "damaged", assets["SN-C-001"]["status"])
check("冻结资金在扣款前仍然保留",
      must(client.get(f"/api/orders/{o1['id']}"))["balances"]["frozen"] == 3000)

# 已结案争议不能重复复检
again = client.post(f"/api/orders/{o1['id']}/disputes/{d1_id}/recheck", json={
    "staff_id": s2.id, "result": "rejected"})
check("已结案争议不能再次复检（409）", again.status_code == 409)

print("\n=== 场景5：逐项报价 + 重复扣款不得再次扣减 ===")
for reason, amount in [("前组镜片划伤修复", 1800), ("对焦模组校准工时", 800)]:
    must(client.post(f"/api/orders/{o1['id']}/disputes/{d1_id}/quotes",
                     json={"reason": reason, "amount": amount}), 201)

ded1 = must(client.post(f"/api/orders/{o1['id']}/disputes/{d1_id}/deduct", json={
    "staff_id": s3.id, "idempotency_key": "key-c001-001"}), 200)
check("首次扣款成功，金额为报价合计 2600",
      ded1["deducted_now"] and ded1["amount"] == 2600, ded1)
first_txn = ded1["txn_id"]

ded2 = must(client.post(f"/api/orders/{o1['id']}/disputes/{d1_id}/deduct", json={
    "staff_id": s3.id, "idempotency_key": "key-c001-DIFFERENT"}), 200)
check("第二次扣款请求被幂等拦截，未再次扣减",
      ded2["duplicate_request"] and not ded2["deducted_now"]
      and ded2["txn_id"] == first_txn, ded2)

ded3 = must(client.post(f"/api/orders/{o1['id']}/disputes/{d1_id}/deduct", json={
    "staff_id": s3.id, "idempotency_key": "key-c001-001"}), 200)
check("相同幂等键重放仍返回原记录",
      ded3["duplicate_request"] and ded3["txn_id"] == first_txn, ded3)

o1d = must(client.get(f"/api/orders/{o1['id']}"))
deduct_txns = [t for t in o1d["ledger"] if t["txn_type"] == "deduct"]
check("账本中始终只有一笔扣减记录", len(deduct_txns) == 1,
      f"实际 {len(deduct_txns)} 笔")
check("扣减原因逐项可查（报价两项写入账本原因）",
      "前组镜片划伤修复" in deduct_txns[0]["reason"]
      and "对焦模组校准工时" in deduct_txns[0]["reason"])
check("冻结已解冻并完成扣减，账目平衡",
      o1d["balances"] == {"paid": 5000.0, "frozen": 0.0, "deducted": 2600.0,
                          "refunded": 0.0, "available": 2400.0},
      o1d["balances"])

# 设备已全部收回（一件完好、一件确认损坏）→ 订单可退余款
rf = must(client.post(f"/api/orders/{o1['id']}/deposit/refund",
                      data={"staff_id": s3.id}), 200)
check("订单#1 退还余款 2400 并结清",
      rf["refunded"] == 2400 and rf["balances"]["available"] == 0
      and must(client.get(f"/api/orders/{o1['id']}"))["status"] == "settled", rf)

print("\n=== 场景6：换件后的新序列号仍能追到正确出库证据 ===")
o2d = must(client.get(f"/api/orders/{o2['id']}"))
new_line = next(l for l in o2d["lines"] if l["asset"]["serial_number"] == "SN-L-001")
old_line = next(l for l in o2d["lines"] if l["asset"]["serial_number"] == "SN-L-002")
seqs = sorted(e["seq"] for e in o2d["checkout_events"])
check("订单#2 保留两次交接批次（首次出库 + 换件）", seqs == [1, 2], seqs)
check("新件 L-001 的出库证据指向第 2 次交接及经手人王出库",
      new_line["checkout_event_seq"] == 2
      and new_line["checkout_staff"]["id"] == s1.id
      and len(new_line["checkout_checks"]) == 1
      and "换件出库" in new_line["checkout_checks"][0]["notes"], new_line)
check("旧件 L-002 的出库证据仍指向第 1 次交接并标记替换链",
      old_line["checkout_event_seq"] == 1
      and old_line["replaced_by_line_id"] == new_line["id"], old_line)

# 新件 L-001 归还时发现暗伤（原出库证据应锁定第 2 次交接，不是第 1 次）
r = must(client.post(f"/api/orders/{o2['id']}/return", json={
    "staff_id": s2.id,
    "items": [{"serial_number": "SN-L-001", "result": "fail", "focus_test": "fail",
               "notes": "换到手的镜头光圈漏油", "hold_amount": 1000}],
}), 200)
d2_id = r["returned"][0]["dispute_id"]
# 李复检(s2)不是该件原出库员工 → 可以独立复检
rc = must(client.post(f"/api/orders/{o2['id']}/disputes/{d2_id}/recheck", json={
    "staff_id": s2.id, "result": "confirmed", "notes": "确认光圈漏油"}), 200)
check("换入件暗伤可独立复检，证据链对应第 2 次出库",
      rc["dispute"]["status"] == "confirmed"
      and rc["dispute"]["checkout_staff"]["id"] == s1.id, rc["dispute"])
must(client.post(f"/api/orders/{o2['id']}/disputes/{d2_id}/quotes",
                 json={"reason": "光圈组件清洗更换", "amount": 600}), 201)
d2 = must(client.post(f"/api/orders/{o2['id']}/disputes/{d2_id}/deduct", json={
    "staff_id": s3.id, "idempotency_key": "key-l001-001"}), 200)
check("订单#2 扣款 600", d2["amount"] == 600 and d2["deducted_now"], d2)
d2b = must(client.post(f"/api/orders/{o2['id']}/disputes/{d2_id}/deduct", json={
    "staff_id": s3.id, "idempotency_key": "key-l001-001"}), 200)
check("订单#2 重复扣款同样不二次扣减", d2b["duplicate_request"], d2b)

# 机身 C-002 完好独立归还后订单结清
must(client.post(f"/api/orders/{o2['id']}/return", json={
    "staff_id": s2.id,
    "items": [{"serial_number": "SN-C-002", "result": "pass"}],
}), 200)
rf2 = must(client.post(f"/api/orders/{o2['id']}/deposit/refund",
                       data={"staff_id": s3.id}), 200)
b2 = must(client.get(f"/api/orders/{o2['id']}"))["balances"]
check("订单#2 账目平衡：付3000 / 扣600 / 退2400",
      b2 == {"paid": 3000.0, "frozen": 0.0, "deducted": 600.0,
             "refunded": 2400.0, "available": 0.0}, b2)

print("\n=== 场景7：暂扣设备核实去向后解除（仍需人工操作，不自动入库）===")
w = next(x for x in must(client.get("/api/wrong-returns?held_only=1"))
         if x["scanned_sn"] == "SN-X-100")
must(client.post(f"/api/wrong-returns/{w['id']}/release",
                 data={"staff_id": s3.id, "note": "已联系客户取回"}), 200)
assets = {a["serial_number"]: a for a in must(client.get("/api/assets"))}
check("解除暂扣后设备才恢复可租", assets["SN-X-100"]["status"] == "available",
      assets["SN-X-100"]["status"])

print("\n=== 场景8：外观照片本地保存并挂接到检查记录 ===")
png = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63f8ffff3f0005fe02fe54d9b8e00000000049454e44ae426082")
up = client.post(
    f"/api/checks/{checkout_check_id}/photos",
    files={"file": ("scratch.png", png, "image/png")},
    data={"label": "出库前组镜片特写"},
)
photo = must(up, 201)
local_path = os.path.join("app", photo["url"].lstrip("/"))
check("照片上传返回访问地址", photo["url"].startswith("/static/uploads/"))
check("照片文件已保存到本地", os.path.exists(local_path), local_path)
detail = must(client.get(f"/api/orders/{o1['id']}"))
photo_urls = [p["url"] for l in detail["lines"] for c in l["checkout_checks"]
              for p in c["photos"]]
check("客户证据页可取到该出库照片（前后并排证据）", photo["url"] in photo_urls)

print(f"\n{'='*56}")
print(f"验证结果：通过 {PASS_COUNT} 项，失败 {FAIL_COUNT} 项")
print(f"{'='*56}")
sys.exit(1 if FAIL_COUNT else 0)
