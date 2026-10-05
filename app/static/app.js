const { createApp } = Vue;

createApp({
  data() {
    return {
      view: "orders",
      staff: [],
      operatorId: 1,
      // 资产
      assets: [],
      assetFilter: "",
      newAsset: { serial_number: "", asset_type: "camera", brand: "", model: "" },
      // 订单
      orders: [],
      currentOrder: null,
      newOrder: { customer_name: "", customer_phone: "", deposit_required: 2000, note: "",
                  items: [{ serial_number: "", focus_test: "normal", notes: "" }] },
      // 归还
      returnForm: { items: [], note: "" },
      // 换件
      replaceForm: { out_serial: "", in_serial: "", out_result: "pass",
                     out_focus_test: "normal", in_focus_test: "normal", note: "" },
      // 复检/报价表单（按争议 id 索引）
      recheckForms: {},
      quoteForms: {},
      // 错还
      wrongReturns: [],
      wrongHeldCount: 0,
      // 提示
      toast: null,
    };
  },
  computed: {
    operatorName() {
      const s = this.staff.find(x => x.id === this.operatorId);
      return s ? s.name : "?";
    },
    filteredAssets() {
      return this.assetFilter ? this.assets.filter(a => a.status === this.assetFilter) : this.assets;
    },
  },
  watch: {
    operatorId() { if (this.currentOrder) this.refreshOrder(); },
  },
  async mounted() {
    await this.loadStaff();
    this.operatorId = this.staff[0]?.id || 1;
    await Promise.all([this.loadAssets(), this.loadOrders()]);
    this.returnForm.items = [this.emptyReturnItem()];
  },
  methods: {
    go(v) { this.view = v; },
    async api(url, opts = {}) {
      const res = await fetch(url, {
        headers: { "Content-Type": "application/json" },
        ...opts,
      });
      let data = null;
      try { data = await res.json(); } catch (e) { /* 非 json */ }
      if (!res.ok) {
        const detail = data?.detail;
        let msg = typeof detail === "string" ? detail : JSON.stringify(detail || data || res.statusText);
        const err = new Error(msg);
        err.status = res.status; err.data = data;
        throw err;
      }
      return data;
    },
    flash(text, type = "ok") {
      this.toast = { text, type };
      setTimeout(() => { this.toast = null; }, 4200);
    },

    async loadStaff() { this.staff = await this.api("/api/staff"); },
    async loadAssets() { this.assets = await this.api("/api/assets"); },
    async loadOrders() { this.orders = await this.api("/api/orders"); },
    async loadWrong() {
      this.wrongReturns = await this.api("/api/wrong-returns?held_only=0");
      this.wrongHeldCount = this.wrongReturns.filter(w => w.held).length;
    },

    assetStatusText(s) {
      return { available: "可租", rented: "在租", disputed: "争议冻结",
               damaged: "确认损坏", wrong_held: "错还暂扣" }[s] || s;
    },
    orderStatusText(s) {
      return { active: "在租/未收齐", returned: "设备已收齐", settled: "押金已结清" }[s] || s;
    },
    lineStatusText(s) {
      return { rented: "在租中", returned_ok: "完好归还", damaged: "确认暗伤",
               disputed: "待复检", replaced: "换件前旧件(已结转)" }[s] || s;
    },
    disputeStatusText(s) {
      return { open: "待复检", confirmed: "确认暗伤", rejected: "复检驳回", settled: "已结清" }[s] || s;
    },
    txnText(t) {
      return { payment: "支付入账", freeze: "冻结", unfreeze: "解冻",
               deduct: "扣减", refund: "退还" }[t] || t;
    },
    focusText(f) { return { normal: "正常", slow: "偏慢", fail: "失败", untested: "未测" }[f] || f; },

    async createAsset() {
      try {
        await this.api("/api/assets", { method: "POST", body: JSON.stringify(this.newAsset) });
        this.newAsset = { serial_number: "", asset_type: "camera", brand: "", model: "" };
        await this.loadAssets();
        this.flash("设备已按序列号登记入库");
      } catch (e) { this.flash(e.message, "err"); }
    },

    async createOrder() {
      if (!this.newOrder.customer_name.trim()) return this.flash("请填写客户姓名", "warn");
      const items = this.newOrder.items.filter(i => i.serial_number.trim());
      if (!items.length) return this.flash("至少登记一个部件序列号", "warn");
      try {
        const order = await this.api("/api/orders", {
          method: "POST",
          body: JSON.stringify({ ...this.newOrder, staff_id: this.operatorId, items }),
        });
        this.flash(`出库完成：${order.order_no}，共 ${items.length} 件逐件登记`);
        this.newOrder = { customer_name: "", customer_phone: "", deposit_required: 2000, note: "",
                          items: [{ serial_number: "", focus_test: "normal", notes: "" }] };
        await this.loadOrders(); await this.loadAssets();
        this.openOrder(order.id);
      } catch (e) {
        if (e.status === 409 && e.data?.detail?.blocked) {
          const list = e.data.detail.blocked.map(b => `${b.serial_number}（${b.reason}）`).join("；");
          this.flash("出库被拦截：" + list, "err");
        } else this.flash(e.message, "err");
      }
    },

    async openOrder(id) {
      await this.refreshOrder(id);
      this.view = "orders";
      this.returnForm = { items: [this.emptyReturnItem()], note: "" };
    },
    async refreshOrder(id) {
      const oid = id || this.currentOrder?.id;
      this.currentOrder = await this.api(`/api/orders/${oid}`);
      for (const d of this.currentOrder.disputes) {
        if (!this.recheckForms[d.id])
          this.recheckForms[d.id] = { result: "confirmed", notes: "" };
        if (!this.quoteForms[d.id])
          this.quoteForms[d.id] = { reason: "", amount: 0 };
      }
      await this.loadOrders();
    },

    emptyReturnItem() {
      return { serial_number: "", result: "pass", focus_test: "normal", notes: "", hold_amount: 0 };
    },

    async submitReturn() {
      const items = this.returnForm.items.filter(i => i.serial_number.trim());
      if (!items.length) return this.flash("请扫描至少一个序列号", "warn");
      try {
        const r = await this.api(`/api/orders/${this.currentOrder.id}/return`, {
          method: "POST",
          body: JSON.stringify({ staff_id: this.operatorId, items, note: this.returnForm.note }),
        });
        let msg = [];
        if (r.returned.length)
          msg.push(`已核对 ${r.returned.length} 件：` +
            r.returned.map(x => `${x.serial_number}(${x.result === "pass" ? "完好" : "暗伤"})`).join("、"));
        if (r.wrong_returns.length)
          msg.push(`⛔ 错还拦截 ${r.wrong_returns.length} 件（暂扣，不入可用库存）：` +
            r.wrong_returns.map(x => x.serial_number).join("、"));
        this.flash(msg.join(" ｜ ") || "无变化", r.wrong_returns.length ? "warn" : "ok");
        this.returnForm = { items: [this.emptyReturnItem()], note: "" };
        await Promise.all([this.refreshOrder(), this.loadAssets(), this.loadWrong()]);
      } catch (e) { this.flash(e.message, "err"); }
    },

    async submitReplace() {
      const f = this.replaceForm;
      if (!f.out_serial.trim() || !f.in_serial.trim())
        return this.flash("请填写交回旧件与换出新件序列号", "warn");
      try {
        const r = await this.api(`/api/orders/${this.currentOrder.id}/replace`, {
          method: "POST", body: JSON.stringify({ staff_id: this.operatorId, ...f }),
        });
        this.flash(`换件交接已重新办理（第 ${r.checkout_event_seq} 次交接）：` +
          `${f.out_serial} → ${f.in_serial}，新件有独立出库证据` +
          (r.dispute_id ? "；旧件暗伤已立争议并冻结押金" : ""), r.dispute_id ? "warn" : "ok");
        this.replaceForm = { out_serial: "", in_serial: "", out_result: "pass",
                             out_focus_test: "normal", in_focus_test: "normal", note: "" };
        await Promise.all([this.refreshOrder(), this.loadAssets()]);
      } catch (e) { this.flash(e.message, "err"); }
    },

    async submitRecheck(d) {
      const f = this.recheckForms[d.id];
      try {
        await this.api(`/api/orders/${this.currentOrder.id}/disputes/${d.id}/recheck`, {
          method: "POST", body: JSON.stringify({ staff_id: this.operatorId, ...f }),
        });
        this.flash(`复检完成：${f.result === "confirmed" ? "确认暗伤，设备停用待报价" : "驳回暗伤，解冻并恢复可租"}`);
        await Promise.all([this.refreshOrder(), this.loadAssets()]);
      } catch (e) {
        this.flash(e.status === 403 ? `独立复检被拒绝：${e.message}` : e.message, "err");
      }
    },

    quoteTotal(d) { return d.quotes.reduce((s, q) => s + q.amount, 0).toFixed(2); },

    async addQuote(d) {
      const f = this.quoteForms[d.id];
      if (!f.reason.trim() || !(f.amount > 0)) return this.flash("请填写扣减原因与金额", "warn");
      try {
        await this.api(`/api/orders/${this.currentOrder.id}/disputes/${d.id}/quotes`, {
          method: "POST", body: JSON.stringify(f),
        });
        this.quoteForms[d.id] = { reason: "", amount: 0 };
        await this.refreshOrder();
      } catch (e) { this.flash(e.message, "err"); }
    },

    async deduct(d, duplicate = false) {
      try {
        const r = await this.api(`/api/orders/${this.currentOrder.id}/disputes/${d.id}/deduct`, {
          method: "POST",
          body: JSON.stringify({
            staff_id: this.operatorId,
            idempotency_key: duplicate ? `client-repeat-${d.id}` : `client-key-${d.id}`,
          }),
        });
        this.flash(r.duplicate_request
          ? `重复扣款请求已拦截：账本中仍只有原扣款 ¥${r.amount}，未再次扣减（幂等成功）`
          : `扣款成功 ¥${r.amount}：${r.note}`,
          r.duplicate_request ? "warn" : "ok");
        await this.refreshOrder();
      } catch (e) { this.flash(e.message, "err"); }
    },

    async payDeposit() {
      try {
        const r = await this.api(`/api/orders/${this.currentOrder.id}/deposit/pay`, {
          method: "POST", body: JSON.stringify({}),
        });
        this.flash(`模拟支付通道：押金到账 ¥${r.balances.paid}（可用 ¥${r.balances.available}）`);
        await this.refreshOrder();
      } catch (e) { this.flash(e.message, "err"); }
    },

    async refundDeposit() {
      try {
        const fd = new FormData(); fd.append("staff_id", this.operatorId);
        const res = await fetch(`/api/orders/${this.currentOrder.id}/deposit/refund`, { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
        this.flash(data.refunded ? `已退还剩余押金 ¥${data.refunded}，订单结清` : "无余款可退，订单已结清");
        await this.refreshOrder(); await this.loadAssets();
      } catch (e) { this.flash(e.message, "err"); }
    },

    async uploadPhoto(checkId, ev) {
      const file = ev.target.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      fd.append("label", file.name);
      try {
        await fetch(`/api/checks/${checkId}/photos`, { method: "POST", body: fd });
        this.flash("外观照已保存到本地并挂到对应检查记录");
        await this.refreshOrder();
      } catch (e) { this.flash("照片上传失败", "err"); }
    },

    async releaseWrong(w) {
      const fd = new FormData();
      fd.append("staff_id", this.operatorId);
      fd.append("note", "已与客户核实设备去向");
      try {
        const res = await fetch(`/api/wrong-returns/${w.id}/release`, { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(JSON.stringify(data.detail || data));
        this.flash("暂扣已解除（设备按其实际租借关系处理，未自动并入任何订单）");
        await Promise.all([this.loadWrong(), this.loadAssets()]);
      } catch (e) { this.flash(e.message, "err"); }
    },

    nextLine(ln) {
      if (!ln.replaced_by_line_id) return null;
      return this.currentOrder.lines.find(x => x.id === ln.replaced_by_line_id);
    },
  },
}).mount("#app");
