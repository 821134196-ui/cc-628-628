# 相机镜头租赁 · 序列号匹配与暗伤争议系统

租赁门店出借/收回相机套装时**逐件**核对机身、镜头及配件（按序列号），
并对暗伤争议进行独立复检与押金冻结/扣减管理。

技术栈：Python + FastAPI + SQLAlchemy + SQLite + Vue 3（本地引入，无构建步骤）。
照片本地保存于 `uploads/`，押金支付为模拟接口（冻结/扣减/释放流水）。

## 一条命令启动

```bash
./start.sh          # 自动建虚拟环境、装依赖、初始化数据库并启动
# 或已装好依赖时：python3 run.py
```

打开 http://127.0.0.1:8000 （API 文档：http://127.0.0.1:8000/docs）

## 运行测试

```bash
.venv/bin/python -m pytest tests/ -q
```

覆盖四条关键规则：

| 规则 | 验证点 |
|---|---|
| 错还拦截 | 归还序列号与出库记录不符 → 409 拦截；错还设备 `quarantined` 隔离，**不进入可用库存**，也不能再被新订单租用 |
| 独立复检权限 | 复检人 == 原出库员工 → 403；其他员工 → 通过 |
| 重复扣款处理 | 同一幂等键重复结算 → 返回原结算单，只扣一次；已结案争议换新键再扣 → 409 |
| 换件证据链 | 换件必须重新办理交接（照片+对焦测试）；客户证据页按槽位展示新旧件，能追到**新序列号自己的出库证据** |

## 业务设计

- **逐件管理**：订单由若干 `OrderItem`（槽位 slot + 序列号资产）组成，出库/归还都按件办理。
- **出库交接**：逐件扫序列号（与订单绑定件不一致则拦截）、拍外观照、记录对焦等功能测试。
- **归还对照**：实扫序列号与出库记录逐件比对；完好件**独立归还**即时回库，不受其他件争议影响。
- **暗伤争议**：登记暗伤 → 设备隔离 + 押金对应金额**冻结** → **非原出库员工**复检 →
  定损结算（扣减原因逐项入账，幂等防重复扣款），剩余冻结释放；暗伤设备转维修，不回可用库存。
- **套装换件**：旧件归还留痕，新件在同一槽位生成新交接记录（`replaces_item_id` 指回旧件），
  客户证据页并排展示同一序列号的前后照片与功能测试。
- **两套记录分离**：设备收回（`ReturnRecord`）与押金结算（`Settlement` / `DepositTransaction`）分开留痕。

## 主要接口

```
POST /api/orders                          创建订单（逐件序列号）
POST /api/orders/{id}/checkout            出库交接（逐件）
POST /api/orders/{id}/returns             归还登记（错还自动拦截隔离）
POST /api/orders/{id}/items/{iid}/swap    套装换件（重新交接）
POST /api/disputes/{id}/reinspect         复检（复检人≠原出库员工）
POST /api/disputes/{id}/settle            定损结算（幂等）
GET  /api/orders/{id}/evidence            客户证据页数据（前后并排）
GET  /api/orders/{id}/deposit             押金台账与流水
POST /api/orders/{id}/close               关闭订单并释放剩余押金
POST /api/uploads                         照片上传（本地保存）
```

## 目录结构

```
app/            FastAPI 后端（models / routers / seed）
static/         Vue 前端（index.html + app.js，本地 vendor Vue）
uploads/        照片本地保存目录
tests/          pytest 业务规则验证
run.py          启动入口（python3 run.py）
start.sh        一条命令启动
```
