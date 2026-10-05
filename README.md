# 相机镜头租赁 · 序列号匹配与暗伤争议系统

租赁门店出借/收回相机套装时逐件核对机身、镜头及状态的全流程系统：
按序列号登记、前后证据并排、错还拦截、临时换件重新交接、暗伤独立复检、
押金冻结/逐项扣减/幂等。

技术栈：**Python + FastAPI + SQLAlchemy + SQLite + Vue 3（本地 vendor，无需联网构建）**。

## 一条本地命令启动

```bash
./run.sh
```

自动完成：建虚拟环境 → 装依赖 → 初始化 SQLite 与种子数据 → 启动服务。

- 前端：http://127.0.0.1:8000/
- API 文档：http://127.0.0.1:8000/docs

> 系统 Python 缺少 `ensurepip` 时，脚本会自动用仓库内 `scripts/get-pip.py` 引导 pip。
> 若 8000 端口被占用：`uvicorn app.main:app --port 8010`（在 `.venv` 激活状态下）。

## 验证

```bash
. .venv/bin/activate
python verify.py     # 使用临时 SQLite，不影响正式数据
```

覆盖 **40 项断言**，包括题目要求的四个关键点：

| 验证点 | 预期行为 |
|---|---|
| 错还拦截 | 未知/非本订单在租序列号被拦截，设备置为 `wrong_held` 暂扣，**不进入可用库存**，人工核实后才能解除 |
| 独立复检权限 | 暗伤必须由**非该件原出库员工**复检，原员工提交返回 403；其他员工可确认/驳回 |
| 重复扣款 | 同一争议仅允许一笔扣减，重复请求（即使换新幂等键）返回原扣款流水，**不二次扣减** |
| 换件证据链 | 换件生成新的交接批次（seq=2），新序列号挂在当次出库员工/检查/照片上；旧件保留第 1 次交接证据与替换链 |

另验证：完好部件可独立归还并立即再租（不等整套争议结束）、争议未决先冻结押金、
报价逐项展示、收回记录与押金账本分离、照片本地保存并出现在客户并排证据页。

## 业务规则与数据模型

```
Order 订单
 ├─ CheckoutEvent 交接批次（seq=1 首次出库；每次临时换件 seq+1，重新办理交接）
 │   └─ OrderLine 借出部件行（每个序列号一行，状态机 rented→returned_ok/damaged/disputed/replaced）
 │        └─ CheckRecord 检查（checkout/return/recheck）+ Photo 本地照片
 ├─ ReturnEvent 归还批次（与押金结算分别留记录）
 ├─ WrongReturn 错还暂扣（设备不得进入可用库存）
 ├─ DefectDispute 暗伤争议 → Recheck 独立复检（员工 ≠ 原出库员工）
 │                        → DeductionQuote 报价逐项
 └─ DepositTxn 押金账本：payment/freeze/unfreeze/deduct/refund
    （dispute 维度唯一扣款 + idempotency_key 双保险，重复请求幂等）
```

押金恒等式：`已付 = 可用 + 冻结 + 已扣减 + 已退`。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/orders` | 按序列号列表逐件出库（任一不可用整单 409 拦截并列出原因） |
| POST | `/api/orders/{id}/return` | 逐件归还核对，返回中区分 `returned` 与 `wrong_returns` |
| POST | `/api/orders/{id}/replace` | 临时换件：旧件回收检查 + 新批次交接 |
| POST | `/api/orders/{id}/disputes/{did}/recheck` | 独立复检（原出库员工 403） |
| POST | `/api/orders/{id}/disputes/{did}/quotes` | 逐项报价（原因+金额） |
| POST | `/api/orders/{id}/disputes/{did}/deduct` | 扣减，重复请求幂等返回原记录 |
| POST | `/api/orders/{id}/deposit/pay` | 模拟支付通道押金入账 |
| POST | `/api/orders/{id}/deposit/refund` | 争议清零、设备收齐后退还余款 |
| POST | `/api/checks/{cid}/photos` | 外观照片上传，保存在 `app/static/uploads/` |
| GET  | `/api/wrong-returns` | 错还暂扣列表 |

## 目录结构

```
app/
  main.py        FastAPI 路由与业务规则
  models.py      SQLAlchemy 模型
  schemas.py     Pydantic 结构
  deposit.py     模拟支付通道 + 押金账本（冻结/扣减/退款/幂等）
  seed.py        种子数据（3 名员工、8 件序列号设备）
  database.py    SQLite（可用 RENTAL_DB 环境变量覆盖库路径）
  static/        Vue 3 单页（vendor/vue.global.prod.js 本地化）+ 上传照片
verify.py        端到端验证（TestClient，临时库）
run.sh           一条命令启动
scripts/get-pip.py  无 ensurepip 环境下引导 pip
data/rental.db   运行期 SQLite（自动生成，已 gitignore）
```

## 种子账号

王出库（员工，负责首次出库）、李复检（员工，独立复检人）、赵店长（经理）。
前端右上角切换当前操作员工，可直接体验“原出库员工复检被拒 → 切换员工复检”的权限隔离。
