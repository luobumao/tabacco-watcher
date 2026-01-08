import json
import os

SETTINGS_FILE = "bot_settings.json"

DEFAULT_SETTINGS = {
    "enable_main_service": True,         # [新增] 主服务总开关
    "enable_hourly_restock_info": True,  # Whether to show stock info (list) in hourly dashboard
    "auto_pin_dashboard": True,
    "dashboard_caption_template": "📅 {time} 库存看板\n📦 有货: {in_stock_count} | ❌ 缺货: {out_stock_count}",
    "show_out_of_stock_list": True,
    "dashboard_header_template": "华盛烟丝库存看板"
}

class BotSettings:
    def __init__(self):
        self.settings = self._load_settings()

    def _load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    return {**DEFAULT_SETTINGS, **json.load(f)}
            except:
                pass
        return DEFAULT_SETTINGS.copy()

    def save_settings(self):
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to save settings: {e}")

    def get(self, key):
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def set(self, key, value):
        self.settings[key] = value
        self.save_settings()

# Singleton instance
settings = BotSettings()