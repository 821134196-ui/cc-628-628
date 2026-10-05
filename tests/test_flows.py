"""业务规则验证：

1. 错还拦截：序列号对不上 → 拒绝归还，错还设备隔离、不进入可用库存。
2. 独立复检权限：复检人不得为原出库员工。
3. 重复扣款处理：同一幂等键重复结算只扣一次；已结案争议不得再扣。
4. 换件追溯：换件后客户证据页仍能追到正确的出库证据。
另覆盖：出库序列号核对、完好件独立归还、押金冻结/扣减/释放台账。
"""
import base64

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEh"
    "QGAhKmMIQAAAABJRU5ErkJggg==")

BODY = "SN-BODY-A7M4-001"
LENS_A = "SN-LENS-2470-001"
LENS_B = "SN-LENS-50-001"
LENS_OTHER = "SN-LENS-2470-009"  # 另一支同型号镜头，用于错还演示


# ----------  helpers  ----------

def make_order(client, serials, deposit=5000.0):
    r = client.post("/api/orders", json={
        "customer_id": 1, "deposit_amount": deposit, "serials": serials})
    assert r.status_code == 200, r.text
    return r.json()


def checkout_all(client, order_id, employee_id=1):
    order = client.get(f"/api/orders/{order_id}").json()
    for item in order["items"]:
        r = client.post(f"/api/orders/{order_id}/checkout", json={
            "item_id": item["item_id"], "employee_id": employee_id,
            "serial_scanned": item["asset"]["serial"],
            "photos": ["/uploads/out.jpg"],
            "function_tests": {"focus": True, "aperture": True},
            "notes": "出库外观完好"})
        assert r.status_code == 200, r.text
    return client.get(f"/api/orders/{order_id}").json()


def get_asset(client, serial):
    return next(a for a in client.get("/api/assets").json()
                if a["serial"] == serial)


def return_item(client, order_id, serial, employee_id=2, **kw):
    payload = {"employee_id": employee_id, "serial_scanned": serial,
               "condition": "intact", "photos": ["/uploads/back.jpg"]}
    payload.update(kw)
    return client.post(f"/api/orders/{order_id}/returns", json=payload)


# ----------  1. 错还拦截  ----------

def test_wrong_return_intercepted_and_quarantined(client):
    order = make_order(client, [BODY, LENS_A])
    checkout_all(client, order["id"], employee_id=1)

    # 客户拿同型号另一支镜头（SN-...-009）来还 → 拦截
    r = return_item(client, order["id"], LENS_OTHER)
    assert r.status_code == 409
    assert "错还拦截" in r.json()["detail"]

    # 错还设备被隔离，不进入可用库存
    assert get_asset(client, LENS_OTHER)["status"] == "quarantined"
    # 隔离设备不能再被新订单租用
    r2 = client.post("/api/orders", json={
        "customer_id": 2, "deposit_amount": 1000, "serials": [LENS_OTHER]})
    assert r2.status_code == 409

    # 原订单在租件不受影响：机身可正常独立归还
    r3 = return_item(client, order["id"], BODY)
    assert r3.status_code == 200
    assert get_asset(client, BODY)["status"] == "available"
    # 在租镜头仍是 rented
    assert get_asset(client, LENS_A)["status"] == "rented"

    # 未知序列号同样被拦截
    r4 = return_item(client, order["id"], "SN-NOPE-000")
    assert r4.status_code == 409

    # 证据页留有错还拦截记录
    ev = client.get(f"/api/orders/{order['id']}/evidence").json()
    assert len(ev["wrong_returns"]) == 2
    assert all(w["result"] == "wrong_serial" for w in ev["wrong_returns"])


def test_checkout_serial_mismatch_blocked(client):
    order = make_order(client, [LENS_A])
    item_id = order["items"][0]["item_id"]
    r = client.post(f"/api/orders/{order['id']}/checkout", json={
        "item_id": item_id, "employee_id": 1,
        "serial_scanned": LENS_OTHER,  # 扫错序列号
        "photos": ["/uploads/out.jpg"], "function_tests": {"focus": True}})
    assert r.status_code == 400
    assert "序列号不符" in r.json()["detail"]
    # 缺少对焦测试记录也不允许出库
    r2 = client.post(f"/api/orders/{order['id']}/checkout", json={
        "item_id": item_id, "employee_id": 1, "serial_scanned": LENS_A,
        "photos": ["/uploads/out.jpg"], "function_tests": {"aperture": True}})
    assert r2.status_code == 400


# ----------  2. 独立复检权限  ----------

def _open_dispute(client, deposit=5000.0, freeze=2000.0, checkout_by=1):
    order = make_order(client, [LENS_A], deposit=deposit)
    checkout_all(client, order["id"], employee_id=checkout_by)
    r = return_item(client, order["id"], LENS_A,
                    condition="hidden_damage",
                    damage_description="镜片内部霉丝，出库照片无此痕迹",
                    freeze_amount=freeze)
    assert r.status_code == 200, r.text
    disputes = client.get("/api/disputes").json()
    assert len(disputes) == 1 and disputes[0]["status"] == "open"
    return order["id"], disputes[0]["id"]


def test_reinspection_must_be_independent(client):
    order_id, dispute_id = _open_dispute(client, checkout_by=1)

    # 原出库员工（张伟 id=1）复检 → 拒绝
    r = client.post(f"/api/disputes/{dispute_id}/reinspect",
                    json={"inspector_id": 1, "damage_confirmed": True})
    assert r.status_code == 403
    assert "独立复检" in r.json()["detail"]

    # 其他员工（李娜 id=2）复检 → 通过
    r2 = client.post(f"/api/disputes/{dispute_id}/reinspect",
                     json={"inspector_id": 2, "damage_confirmed": True,
                           "notes": "复检确认霉丝"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "reinspected"

    # 争议期间资金保持冻结
    ledger = client.get(f"/api/orders/{order_id}/deposit").json()
    assert ledger["frozen_amount"] == 2000.0
    assert ledger["deducted_amount"] == 0.0


def test_reinspection_reject_releases_freeze(client):
    order_id, dispute_id = _open_dispute(client)
    r = client.post(f"/api/disputes/{dispute_id}/reinspect",
                    json={"inspector_id": 2, "damage_confirmed": False,
                          "notes": "出库照片已有同款痕迹，非租期损伤"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    ledger = client.get(f"/api/orders/{order_id}/deposit").json()
    assert ledger["frozen_amount"] == 0.0
    assert ledger["released_amount"] == 2000.0
    # 设备解除隔离
    assert get_asset(client, LENS_A)["status"] == "available"


# ----------  3. 重复扣款处理  ----------

def test_duplicate_deduction_is_idempotent(client):
    order_id, dispute_id = _open_dispute(client, deposit=5000.0, freeze=2000.0)
    client.post(f"/api/disputes/{dispute_id}/reinspect",
                json={"inspector_id": 2, "damage_confirmed": True})

    settle_body = {
        "items": [{"reason": "镜片霉丝清理", "amount": 300},
                  {"reason": "镜组校准", "amount": 500}],
        "idempotency_key": "settle-req-001"}
    r1 = client.post(f"/api/disputes/{dispute_id}/settle", json=settle_body)
    assert r1.status_code == 200 and r1.json()["duplicate"] is False
    assert r1.json()["dispute"]["settlement"]["total"] == 800.0
    # 扣减原因逐项留痕
    reasons = [i["reason"] for i in r1.json()["dispute"]["settlement"]["items"]]
    assert reasons == ["镜片霉丝清理", "镜组校准"]

    # 同一请求重发（网络重试/双击）→ 不重复扣款
    r2 = client.post(f"/api/disputes/{dispute_id}/settle", json=settle_body)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True

    ledger = client.get(f"/api/orders/{order_id}/deposit").json()
    deduct_txns = [t for t in ledger["transactions"] if t["type"] == "deduct"]
    assert len(deduct_txns) == 1                    # 只有一笔扣减流水
    assert ledger["deducted_amount"] == 800.0       # 只扣了一次
    assert ledger["frozen_amount"] == 0.0           # 冻结已清
    assert ledger["released_amount"] == 1200.0      # 剩余冻结释放

    # 换一个幂等键对已结案争议再扣 → 拒绝
    r3 = client.post(f"/api/disputes/{dispute_id}/settle", json={
        "items": [{"reason": "二次扣款", "amount": 100}],
        "idempotency_key": "settle-req-002"})
    assert r3.status_code == 409
    ledger2 = client.get(f"/api/orders/{order_id}/deposit").json()
    assert ledger2["deducted_amount"] == 800.0      # 未被二次扣减

    # 暗伤设备转维修，不进入可用库存
    assert get_asset(client, LENS_A)["status"] == "maintenance"


def test_settle_exceeding_freeze_rejected(client):
    _, dispute_id = _open_dispute(client, freeze=1000.0)
    client.post(f"/api/disputes/{dispute_id}/reinspect",
                json={"inspector_id": 3, "damage_confirmed": True})
    r = client.post(f"/api/disputes/{dispute_id}/settle", json={
        "items": [{"reason": "超赔", "amount": 1500}],
        "idempotency_key": "k-over"})
    assert r.status_code == 400


# ----------  4. 换件后证据链追溯  ----------

def test_swap_keeps_correct_checkout_evidence(client):
    order = make_order(client, [BODY, LENS_A])
    checkout_all(client, order["id"], employee_id=1)
    lens_item = next(i for i in order["items"]
                     if i["asset"]["serial"] == LENS_A)

    # 换件必须重新办理交接：缺对焦测试 → 拒绝
    r0 = client.post(
        f"/api/orders/{order['id']}/items/{lens_item['item_id']}/swap",
        json={"employee_id": 1, "new_serial": LENS_B,
              "photos": ["/uploads/swap.jpg"], "function_tests": {}})
    assert r0.status_code == 400

    # 正常换件：LENS_A → LENS_B，新件重新拍照+功能测试
    r = client.post(
        f"/api/orders/{order['id']}/items/{lens_item['item_id']}/swap",
        json={"employee_id": 1, "new_serial": LENS_B,
              "photos": ["/uploads/swap-1.jpg", "/uploads/swap-2.jpg"],
              "function_tests": {"focus": True, "aperture": True},
              "notes": "客户要求换成 50mm 定焦"})
    assert r.status_code == 200, r.text

    # 客户证据页：槽位 2 当前件为 LENS_B，且能追到它自己的出库证据
    ev = client.get(f"/api/orders/{order['id']}/evidence").json()
    slot2 = next(s for s in ev["slots"] if s["slot"] == 2)
    assert slot2["swapped"] is True
    assert slot2["current_serial"] == LENS_B
    assert len(slot2["items"]) == 2

    old_item, new_item = slot2["items"]
    assert old_item["asset"]["serial"] == LENS_A
    assert old_item["status"] == "swapped"
    assert old_item["checkout"]["serial_scanned"] == LENS_A  # 旧件出库证据仍在
    assert new_item["replaces_item_id"] == old_item["item_id"]
    assert new_item["checkout"]["serial_scanned"] == LENS_B  # 新件出库证据正确
    assert new_item["checkout"]["photos"] == ["/uploads/swap-1.jpg",
                                              "/uploads/swap-2.jpg"]
    assert new_item["checkout"]["function_tests"]["focus"] is True

    # 归还时按新序列号对照：还 LENS_B 正常匹配
    r2 = return_item(client, order["id"], LENS_B)
    assert r2.status_code == 200
    # 旧序列号已被替换，再还 → 拦截（已处理）
    r3 = return_item(client, order["id"], LENS_A)
    assert r3.status_code == 409
    # 旧件换件后已回可用库存
    assert get_asset(client, LENS_A)["status"] == "available"


# ----------  5. 完好件独立归还 + 全流程  ----------

def test_intact_items_return_independently_during_dispute(client):
    order = make_order(client, [BODY, LENS_A], deposit=5000.0)
    checkout_all(client, order["id"], employee_id=1)

    # 镜头暗伤 → 争议未决
    r = return_item(client, order["id"], LENS_A, condition="hidden_damage",
                    damage_description="对焦马达异响", freeze_amount=1500)
    assert r.status_code == 200

    # 机身完好 → 独立归还，不必等整套争议处理结束
    r2 = return_item(client, order["id"], BODY)
    assert r2.status_code == 200
    assert get_asset(client, BODY)["status"] == "available"

    # 争议未决不能关闭订单
    rc = client.post(f"/api/orders/{order['id']}/close")
    assert rc.status_code == 409

    # 复检 → 结算 → 关闭，押金台账完整
    disputes = client.get("/api/disputes").json()
    did = disputes[0]["id"]
    client.post(f"/api/disputes/{did}/reinspect",
                json={"inspector_id": 2, "damage_confirmed": True})
    client.post(f"/api/disputes/{did}/settle", json={
        "items": [{"reason": "对焦马达维修", "amount": 900}],
        "idempotency_key": "settle-flow-1"})
    rc2 = client.post(f"/api/orders/{order['id']}/close")
    assert rc2.status_code == 200

    ledger = client.get(f"/api/orders/{order['id']}/deposit").json()
    assert ledger["deducted_amount"] == 900.0
    # 释放 = 争议剩余 600 + 关闭时释放 3500
    assert ledger["released_amount"] == 4100.0
    types = [t["type"] for t in ledger["transactions"]]
    assert types == ["freeze", "deduct", "release", "release"]


# ----------  6. 照片上传  ----------

def test_photo_upload_saved_locally(client):
    r = client.post("/api/uploads",
                    files={"file": ("外观.jpg", PNG_1PX, "image/jpeg")})
    assert r.status_code == 200
    path = r.json()["path"]
    assert path.startswith("/uploads/")
    # 可通过静态路径访问
    r2 = client.get(path)
    assert r2.status_code == 200 and r2.content == PNG_1PX
    # 非法类型拒绝
    r3 = client.post("/api/uploads",
                     files={"file": ("x.txt", b"hello", "text/plain")})
    assert r3.status_code == 400
