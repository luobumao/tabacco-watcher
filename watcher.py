import requests
import re
import json
import html
import os
import time
import datetime
import threading
import random
# [修改] 移除了 hashlib，不再需要
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

# 本地模块
from config import SELECTOR_HUASHENG, CHECK_INTERVAL
from settings import settings
from notifier import TelegramNotifier
from renderer import renderer

# 常量定义
STATUS_FILE = "stock_status.json"
PRODUCTS_FILE = "products.json"

class HuashengWatcher:
    def __init__(self):
        self.session = self._init_session()
        self.ua = UserAgent()
        self.notifier = TelegramNotifier(self.session)
        self.lock = threading.RLock()
        
        self.watch_list = self._load_products()
        self.stock_history = self._load_history()
        
        # 启动时清理过时数据
        self._clean_obsolete_data()
        
        self.start_time = datetime.datetime.now()
        self.next_hourly_ts = self._get_next_hour_timestamp()
        
        self.is_first_run = not bool(self.stock_history)
        self.consecutive_errors = 0
        
        self.notifier.set_refresh_callback(self._force_refresh_dashboard)
        self.notifier.set_reset_callback(self._reset_stock_data)

    def _init_session(self):
        s = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        s.mount('https://', HTTPAdapter(max_retries=retries))
        return s

    def _load_products(self):
        if os.path.exists(PRODUCTS_FILE):
            try:
                with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if not isinstance(data, list):
                        print(f"❌ {PRODUCTS_FILE} 格式错误: 必须是列表")
                        return []
                    return data
            except Exception as e:
                print(f"❌ 读取配置文件失败: {e}")
                return []
        return []

    def _load_history(self):
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: pass
        return {}

    def save_history(self):
        with self.lock:
            try:
                with open(STATUS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.stock_history, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"❌ 保存状态失败: {e}")
    
    def _get_next_hour_timestamp(self):
        now = datetime.datetime.now()
        next_hour = (now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1))
        return next_hour.timestamp()

    def _clean_obsolete_data(self):
        """清理已删除的分类数据"""
        with self.lock:
            if not self.watch_list: 
                return False

            valid_categories = set([item['name'] for item in self.watch_list])
            ids_to_delete = []
            
            for pid, data in self.stock_history.items():
                if pid.startswith('_'): continue
                product_category = data.get('category')
                if product_category and product_category not in valid_categories:
                    ids_to_delete.append(pid)
            
            if ids_to_delete:
                print(f"🧹 [清理] 移除 {len(ids_to_delete)} 个已删除分类的残留商品")
                for pid in ids_to_delete:
                    del self.stock_history[pid]
                self.save_history()
                return True
            return False

    def fetch_page(self, url):
        try:
            timestamp = int(time.time() * 1000)
            target = f"{url}{'&' if '?' in url else '?'} _t={timestamp}"
            headers = {"User-Agent": self.ua.random}
            resp = self.session.get(target, headers=headers, timeout=15)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            print(f"❌ 网络请求失败 [{url}]: {e}")
            return None

    def _normalize_unit(self, name):
        return re.sub(r'(\d{2,3})\s*克', r'\1g', name)

    def _scan_site_list(self, item):
        # time.sleep(random.uniform(0.5, 1.0))
        url = item['url']
        html = self.fetch_page(url)
        
        if not html:
            return True, [] 

        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.select(SELECTOR_HUASHENG['product_card'])
        
        if not cards:
            print(f"⚠️ [解析警告] 分类 '{item['name']}' 未找到商品，请检查URL或页面结构。")
            return False, []

        updates = []
        for card in cards:
            name_elem = card.select_one(SELECTOR_HUASHENG['product_name'])
            if not name_elem: continue
            raw_name = name_elem.get_text(strip=True)
            name = self._normalize_unit(raw_name)
            
            product_url = name_elem.get('href', url)
            data_id = card.get('data-id')
            product_id = f"HS_{data_id}" if data_id else f"HS_{abs(hash(product_url))}"
            
            img_elem = card.select_one(SELECTOR_HUASHENG['product_image'])
            img_url = None
            if img_elem:
                img_url = img_elem.get('data-lazy-src') or img_elem.get('srcset', '').split(',')[0].split(' ')[0] or img_elem.get('data-src') or img_elem.get('src')

            # ================= 库存判定逻辑 =================
            is_sold_out = False 
            card_classes = card.get('class', [])
            
            if 'outofstock' in card_classes:
                is_sold_out = True
            elif card.select_one('.out-of-stock.product-label'):
                is_sold_out = True
            elif 'instock' in card_classes:
                is_sold_out = False
            else:
                btn = card.select_one(SELECTOR_HUASHENG['status_button'])
                if btn:
                    btn_text = btn.get_text(strip=True)
                    if "阅读更多" in btn_text:
                        is_sold_out = True
                    else:
                        is_sold_out = False
            # =========================================================
            
            updates.append({
                "id": product_id,
                "name": name,
                "url": product_url,
                "img": img_url,
                "is_sold_out": is_sold_out,
                "category": item['name']
            })
        
        return False, updates

    def _process_updates(self, updates):
        restocks = []
        status_changed = False

        with self.lock:
            for item in updates:
                pid = item['id']
                is_sold_out = item['is_sold_out']
                
                # 检测是否为新商品
                is_new_product = pid not in self.stock_history
                
                old_record = self.stock_history.get(pid, {})
                was_sold_out = old_record.get('is_sold_out', True)
                
                # 状态改变：库存变动 OR 新商品入库
                if (is_sold_out != was_sold_out) or is_new_product:
                    status_changed = True
                    if is_new_product:
                        print(f"✨ 新商品入库: {item['name']} (状态: {'缺货' if is_sold_out else '有货'})")

                record = {
                    "name": item['name'],
                    "url": item['url'],
                    "img": item['img'],
                    "is_sold_out": is_sold_out,
                    "category": item['category'],
                    "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                self.stock_history[pid] = record
                
                # 补货提醒 (仅当从无货变为有货时)
                if not is_sold_out and was_sold_out:
                    restocks.append(record)
                    
        return restocks, status_changed

    def _send_restock_alert(self, item):
        safe_name = html.escape(item['name'])
        safe_category = html.escape(item['category'])
        caption = (
            f"🚨 <b>补货通知</b>\n\n"
            f"📦 <b>{safe_name}</b>\n"
            f"📂 系列: {safe_category}\n"
            f"🔗 <a href='{item['url']}'>点击立即购买</a>"
        )
        if item.get('img'):
            self.notifier.send_photo(item['img'], caption)
        else:
            self.notifier.send_message(caption)

    def _force_refresh_dashboard(self):
        print("👆 手动刷新看板...")
        self._send_dashboard(is_hourly_update=False, is_force_resend=True)

    def _reset_stock_data(self):
        with self.lock:
            try:
                if os.path.exists(STATUS_FILE): os.remove(STATUS_FILE)
                self.stock_history = {}
                self.is_first_run = True
                if '_dashboard_ids' in self.stock_history: del self.stock_history['_dashboard_ids']
                print("🗑️ 数据已重置")
            except: pass

    def _send_dashboard(self, is_hourly_update=False, is_force_resend=False):
        with self.lock:
            products = [v for k, v in self.stock_history.items() if not k.startswith('_')]
            if not products: return

            render_list = products
            if not settings.get("show_out_of_stock_list"):
                render_list = [p for p in products if not p['is_sold_out']]

            real_in_stock = len([p for p in products if not p['is_sold_out']])
            real_out_stock = len([p for p in products if p['is_sold_out']])

            try:
                img_list = renderer.render_group(render_list, full_in_stock=real_in_stock, full_out_stock=real_out_stock)
            except Exception as e:
                print(f"⚠️ 看板生成失败: {e}")
                return
            
            tpl = settings.get("dashboard_caption_template")
            try:
                caption = tpl.format(time=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), in_stock_count=real_in_stock, out_stock_count=real_out_stock)
            except:
                caption = f"库存看板\n有货: {real_in_stock} | 缺货: {real_out_stock}"

            old_ids = self.stock_history.get('_dashboard_ids', [])
            success = False
            
            # [编辑逻辑] 只要不是强制重发，且图片数量匹配，就尝试编辑
            if not is_force_resend and old_ids and len(old_ids) == len(img_list):
                print(f"✏️ 更新现有看板 (共 {len(old_ids)} 张)...")
                all_edit_ok = True
                for idx, msg_id in enumerate(old_ids):
                    current_caption = caption if idx == 0 else None
                    if not self.notifier.edit_media_group_item(msg_id, img_list[idx][1], caption=current_caption):
                        all_edit_ok = False; break
                if all_edit_ok: success = True
            
            # [重发逻辑] 编辑失败、图片数量变化、或强制重发时执行
            if not success:
                if old_ids:
                    # 先删旧的，防止聊天记录混乱
                    for mid in old_ids: self.notifier.delete_message(mid)
                print("📤 发送新看板图集...")
                resp = self.notifier.send_media_group(img_list[:10], caption=caption)
                if resp and isinstance(resp, dict) and resp.get('ok'):
                    new_ids = [m['message_id'] for m in resp['result']]
                    self.stock_history['_dashboard_ids'] = new_ids
                    if settings.get("auto_pin_dashboard") and new_ids:
                        try: self.session.post(f"{self.notifier.api_base}/pinChatMessage", json={"chat_id": self.notifier.chat_id, "message_id": new_ids[0]})
                        except: pass
                else:
                    self.stock_history['_dashboard_ids'] = []

            if is_hourly_update:
                self.next_hourly_ts = self._get_next_hour_timestamp()
            self.save_history()

    def run(self):
        self.notifier.start_polling()
        print(f"🚀 华盛监控服务启动 | 间隔: {CHECK_INTERVAL}s")
        
        while True:
            try:
                if not settings.get("enable_main_service"):
                    time.sleep(CHECK_INTERVAL)
                    continue

                print(f"🔄 [扫描] {datetime.datetime.now().strftime('%H:%M:%S')}")
                
                # 1. 重新加载配置 (确保能读到新添加的 JSON 条目)
                self.watch_list = self._load_products()
                
                # 2. 清理已删除的商品
                data_cleaned = self._clean_obsolete_data()
                
                # 3. 扫描并发处理
                all_restocks = []
                has_error = False
                any_status_change = False

                executor = ThreadPoolExecutor(max_workers=10)
                try:
                    futures = [executor.submit(self._scan_site_list, item) for item in self.watch_list]
                    for future in as_completed(futures):
                        try:
                            err, updates = future.result()
                            if err: has_error = True
                            if updates:
                                restocks, changed = self._process_updates(updates)
                                all_restocks.extend(restocks)
                                if changed: any_status_change = True
                        except: pass
                finally:
                    executor.shutdown(wait=False)
                
                # ================= 触发逻辑 =================
                
                dashboard_trigger_msg = None
                is_hourly = False
                force_new = False
                now_ts = time.time()
                
                # A. 整点强制更新
                if settings.get("enable_hourly_restock_info") and (now_ts >= self.next_hourly_ts):
                    dashboard_trigger_msg = "⏰ 触发整点看板推送..."
                    is_hourly = True
                    force_new = True
                
                # B. 事件驱动更新 (库存变动 / 新货入库 / 删除分类)
                # 注意：force_new 默认为 False，即优先尝试编辑
                elif (any_status_change or data_cleaned) and not self.is_first_run:
                    dashboard_trigger_msg = "🔄 检测到变动，更新看板..."
                
                if dashboard_trigger_msg:
                    print(dashboard_trigger_msg)
                    # 1. 先发看板
                    self._send_dashboard(is_hourly_update=is_hourly, is_force_resend=force_new)
                    
                    # 2. 稍作延迟，防止消息乱序
                    if all_restocks:
                        time.sleep(2)

                # 3. 再发补货通知
                if all_restocks:
                    if self.is_first_run:
                        print(f"✅ [初始化] 发现 {len(all_restocks)} 个有货商品")
                        self.is_first_run = False
                    else:
                        print(f"⚡ 发现 {len(all_restocks)} 个补货，推送通知...")
                        for item in all_restocks:
                            self._send_restock_alert(item)
                            time.sleep(1)
                
                self.consecutive_errors = 0 if not has_error else self.consecutive_errors + 1
                    
            except Exception as e:
                print(f"⚠️ 主循环异常: {e}")
                
            time.sleep(CHECK_INTERVAL)