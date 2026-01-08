import os
from dotenv import load_dotenv

load_dotenv()

# ================= 站点配置 =================

# 华盛烟丝 Selectors
SELECTOR_HUASHENG = {
    # 列表页最外层容器
    "product_card": "div.wd-product",
    
    # 标题链接
    "product_name": "h3.wd-entities-title a",
    
    # 图片
    "product_image": "div.product-element-top a.product-image-link img",
    
    # 状态按钮
    "status_button": "div.wd-add-btn a",
    "in_stock_text": "加入购物车", 
    
    # 辅助判断
    "out_of_stock_label": "span.out-of-stock.product-label"
}

# ================= 系统配置 =================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# [修改] 支持多个管理员ID，用英文逗号分隔
# 例如: .env 中写 ADMIN_USER_ID=123456,987654
raw_admin_ids = os.getenv("ADMIN_USER_ID", "")
ADMIN_USER_IDS = [x.strip() for x in raw_admin_ids.split(',') if x.strip()]

if not TELEGRAM_BOT_TOKEN:
    print("⚠️ 警告: 未在 .env 文件中找到 TELEGRAM_BOT_TOKEN")

if not ADMIN_USER_IDS:
    print("⚠️ 警告: 未设置 ADMIN_USER_ID，管理面板将无法访问")

CHECK_INTERVAL = 60 # 扫描间隔 (秒)