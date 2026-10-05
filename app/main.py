"""FastAPI 应用入口。"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR, UPLOAD_DIR
from .database import Base, engine
from .routers import disputes, evidence, handover, meta, orders
from .seed import seed_if_empty

Base.metadata.create_all(bind=engine)
seed_if_empty()

app = FastAPI(title="相机镜头租赁 · 序列号匹配与暗伤争议系统")


@app.get("/api/health")
def health():
    return {"ok": True}


app.include_router(meta.router)
app.include_router(orders.router)
app.include_router(handover.router)
app.include_router(disputes.router)
app.include_router(evidence.router)

# 照片本地保存并可直接访问
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
# Vue 前端（无构建步骤）；挂载于最后，避免遮蔽 /api 路由
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
