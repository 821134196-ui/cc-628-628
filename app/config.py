"""全局配置：数据库路径与照片上传目录，均可用环境变量覆盖（测试用）。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASE_URL = os.environ.get(
    "RENTAL_DB_URL", f"sqlite:///{os.path.join(BASE_DIR, 'rental.db')}"
)
UPLOAD_DIR = os.environ.get("RENTAL_UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
STATIC_DIR = os.path.join(BASE_DIR, "static")

os.makedirs(UPLOAD_DIR, exist_ok=True)
