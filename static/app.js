/* 相机镜头租赁 · 序列号匹配与暗伤争议系统 — 前端逻辑（Vue 3，无构建步骤） */
const { createApp } = Vue;

async function api(path, { method = "GET", body } = {}) {
  const opts = { method };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(data.detail || `请求失败（${r.status}）`);
    err.status = r.status;
    throw err;
  }
  return data;
}

async function uploadOne(file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/uploads", { method: "POST", body: fd });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || "上传失败");
  return d.path;
}

createApp({
  data() {
    return {
      tab: "assets",
      tabs: [
        { key: "assets", label: "资产库存" },
        { key: "orders", label: "订单管理" },
        { key: "checkout", label: "出库交接" },
        { key: "return", label: "归还验收" },
        { key: "disputes", label: "争议与押金" },
        { key: "evidence", label: "客户证据" },
      ],
      assets: [], employees: [], customers: [], orders: [], disputes: [],
      msg: null,
      zoomSrc: null,
      testDefs: [
        { key: "focus", label: "对焦测试" },
        { key: "aperture", label: "光圈测试" },
        { key: "stabilization", label: "防抖测试" },
        { key: "zoom", label: "变焦测试" },
      ],
      newOrder: { customer_id: 1, deposit_amount: 5000, serials: "" },
      orderDetail: null,
      // 出库
      coOrderId: null, coOrder: null, coForms: {},
      // 归还
      rtOrderId: null, rtOrder: null, rtError: null,
      rtForm: this.blankReturn(),
      swapForm: null,
      // 争议
      riForms: {}, stForms: {},
      // 台账
      ledgerOrderId: null, ledger: null,
      // 证据
      evOrderId: null, evidence: null,
    };
  },
  computed: {
    pendingItems() {
      if (!this.coOrder) return [];
      return this.coOrder.items.filter((i) => i.status === "pending");
    },
  },
  async mounted() {
    await this.reloadAll();
  },
  methods: {
    blankReturn() {
      return { employee_id: 2, serial_scanned: "", condition: "intact",
               photos: [], notes: "", damage_description: "", freeze_amount: null };
    },
    blankTests() {
      return { focus: false, aperture: false, stabilization: false, zoom: false };
    },
    async reloadAll() {
      const [assets, employees, customers, orders, disputes] = await Promise.all([
        api("/api/assets"), api("/api/employees"), api("/api/customers"),
        api("/api/orders"), api("/api/disputes"),
      ]);
      this.assets = assets; this.employees = employees;
      this.customers = customers; this.orders = orders;
      this.setDisputes(disputes);
    },
    setDisputes(list) {
      this.disputes = list;
      for (const d of list) {
        if (!this.riForms[d.id]) {
          const other = this.employees.find((e) => e.id !== d.checkout_employee_id);
          this.riForms[d.id] = {
            inspector_id: other ? other.id : null,
            damage_confirmed: true, notes: "",
          };
        }
        if (!this.stForms[d.id]) {
          this.stForms[d.id] = {
            items: [{ reason: "", amount: 0 }],
            idempotency_key: `settle-d${d.id}-` + Math.random().toString(36).slice(2, 10),
          };
        }
      }
    },
    switchTab(key) {
      this.tab = key; this.msg = null;
      this.reloadAll();
      if (key === "checkout" && this.coOrderId) this.loadCheckoutOrder();
      if (key === "return" && this.rtOrderId) this.loadReturnOrder();
      if (key === "evidence" && this.evOrderId) this.loadEvidence();
      if (key === "disputes" && this.ledgerOrderId) this.loadLedger();
    },
    toast(text, type = "ok") {
      this.msg = { text, type };
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    async run(fn, okText) {
      try {
        const r = await fn();
        if (okText) this.toast(okText, "ok");
        await this.reloadAll();
        return r;
      } catch (e) {
        this.toast(e.message, "err");
        throw e;
      }
    },
    // ---------- 上传 ----------
    async uploadPhotos(evt, form) {
      const files = Array.from(evt.target.files || []);
      for (const f of files) {
        try { form.photos.push(await uploadOne(f)); }
        catch (e) { this.toast(e.message, "err"); }
      }
      evt.target.value = "";
    },
    zoom(p) { this.zoomSrc = p; },
    // ---------- 订单 ----------
    async createOrder() {
      const serials = this.newOrder.serials.split(/[,，\s]+/).filter(Boolean);
      await this.run(async () => {
        await api("/api/orders", { method: "POST", body: {
          customer_id: this.newOrder.customer_id,
          deposit_amount: this.newOrder.deposit_amount,
          serials,
        }});
        this.newOrder.serials = "";
      }, "订单已创建");
    },
    async viewOrder(id) {
      this.orderDetail = await api(`/api/orders/${id}`);
    },
    async closeOrder(id) {
      await this.run(() => api(`/api/orders/${id}/close`, { method: "POST" }),
                     "订单已关闭，剩余押金已释放");
      if (this.orderDetail && this.orderDetail.id === id) this.viewOrder(id);
    },
    // ---------- 出库 ----------
    async loadCheckoutOrder() {
      if (!this.coOrderId) { this.coOrder = null; return; }
      this.coOrder = await api(`/api/orders/${this.coOrderId}`);
      for (const it of this.coOrder.items) {
        if (!this.coForms[it.item_id]) {
          this.coForms[it.item_id] = {
            employee_id: this.employees[0] ? this.employees[0].id : 1,
            serial_scanned: "", photos: [],
            function_tests: this.blankTests(), notes: "",
          };
        }
      }
    },
    async submitCheckout(it) {
      const f = this.coForms[it.item_id];
      await this.run(async () => {
        await api(`/api/orders/${this.coOrderId}/checkout`, { method: "POST", body: {
          item_id: it.item_id, employee_id: f.employee_id,
          serial_scanned: f.serial_scanned, photos: f.photos,
          function_tests: f.function_tests, notes: f.notes,
        }});
      }, `槽位 ${it.slot} 出库完成`);
      await this.loadCheckoutOrder();
    },
    // ---------- 归还 ----------
    async loadReturnOrder() {
      if (!this.rtOrderId) { this.rtOrder = null; return; }
      this.rtOrder = await api(`/api/orders/${this.rtOrderId}`);
      this.rtError = null;
    },
    async submitReturn() {
      this.rtError = null;
      try {
        await api(`/api/orders/${this.rtOrderId}/returns`, { method: "POST", body: {
          employee_id: this.rtForm.employee_id,
          serial_scanned: this.rtForm.serial_scanned,
          condition: this.rtForm.condition,
          photos: this.rtForm.photos, notes: this.rtForm.notes,
          damage_description: this.rtForm.damage_description,
          freeze_amount: this.rtForm.condition === "hidden_damage"
            ? this.rtForm.freeze_amount : null,
        }});
        this.toast(this.rtForm.condition === "hidden_damage"
          ? "已登记暗伤，争议开启并冻结对应押金" : "归还完成，设备已回可用库存", "ok");
        this.rtForm = this.blankReturn();
        await this.reloadAll();
        await this.loadReturnOrder();
      } catch (e) {
        await this.reloadAll();
        await this.loadReturnOrder();
        // 错还拦截等冲突醒目展示（须在刷新订单后设置，避免被清空）
        this.rtError = e.message;
      }
    },
    // ---------- 换件 ----------
    openSwap(it) {
      this.swapForm = {
        item_id: it.item_id, slot: it.slot, oldSerial: it.asset.serial,
        employee_id: this.employees[0] ? this.employees[0].id : 1,
        new_serial: "", photos: [], function_tests: this.blankTests(), notes: "",
      };
    },
    async submitSwap() {
      const f = this.swapForm;
      await this.run(async () => {
        await api(`/api/orders/${this.rtOrderId}/items/${f.item_id}/swap`, {
          method: "POST", body: {
            employee_id: f.employee_id, new_serial: f.new_serial,
            photos: f.photos, function_tests: f.function_tests, notes: f.notes,
          }});
        this.swapForm = null;
      }, "换件完成：旧件已归还，新件已重新办理出库交接");
      await this.loadReturnOrder();
    },
    // ---------- 争议 ----------
    async submitReinspect(d) {
      const f = this.riForms[d.id];
      await this.run(() => api(`/api/disputes/${d.id}/reinspect`, {
        method: "POST", body: {
          inspector_id: f.inspector_id,
          damage_confirmed: f.damage_confirmed, notes: f.notes,
        }}), "复检已提交");
    },
    async submitSettle(d) {
      const f = this.stForms[d.id];
      const r = await this.run(() => api(`/api/disputes/${d.id}/settle`, {
        method: "POST", body: {
          items: f.items.filter((x) => x.reason && x.amount > 0),
          idempotency_key: f.idempotency_key,
        }}), "结算完成");
      if (r && r.duplicate) this.toast("该请求已处理过（幂等），未重复扣款", "info");
      if (this.ledgerOrderId) this.loadLedger();
    },
    // ---------- 台账 ----------
    async loadLedger() {
      if (!this.ledgerOrderId) { this.ledger = null; return; }
      this.ledger = await api(`/api/orders/${this.ledgerOrderId}/deposit`);
    },
    // ---------- 证据 ----------
    async loadEvidence() {
      if (!this.evOrderId) { this.evidence = null; return; }
      this.evidence = await api(`/api/orders/${this.evOrderId}/evidence`);
    },
    // ---------- 展示辅助 ----------
    employeeName(id) {
      const e = this.employees.find((x) => x.id === id);
      return e ? e.name : `#${id}`;
    },
    fmtTime(t) { return t ? t.replace("T", " ").slice(0, 19) : "—"; },
    categoryLabel(c) {
      return { body: "机身", lens: "镜头", accessory: "配件" }[c] || c;
    },
    statusLabel(s) {
      return { available: "可用", reserved: "已预订", rented: "在租",
               quarantined: "隔离锁定", maintenance: "维修中" }[s] || s;
    },
    itemStatusLabel(s) {
      return { pending: "待出库", out: "在租", returned: "已归还",
               swapped: "已换出", disputed: "争议中" }[s] || s;
    },
    disputeStatusLabel(s) {
      return { open: "待复检", reinspected: "待结算",
               settled: "已结算", rejected: "已驳回" }[s] || s;
    },
    returnResultLabel(s) {
      return { matched_intact: "完好归还", matched_damage: "暗伤",
               wrong_serial: "错还拦截" }[s] || s;
    },
    txnTypeLabel(t) {
      return { freeze: "冻结", deduct: "扣减", release: "释放" }[t] || t;
    },
    testSummary(tests) {
      if (!tests) return "—";
      const names = { focus: "对焦", aperture: "光圈", stabilization: "防抖", zoom: "变焦" };
      return Object.entries(tests)
        .map(([k, v]) => `${names[k] || k}${v ? "✓" : "✗"}`).join("　");
    },
  },
}).mount("#app");
