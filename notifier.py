import requests
import time
import json
import threading
import html
import sys
import os
import re
import math
# [重要] 确保从 config 导入的是 ADMIN_USER_IDS (列表)
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ADMIN_USER_IDS
from settings import settings

PRODUCTS_FILE = "products.json"
PAGE_SIZE = 10  # 每页显示的商品数量

class TelegramNotifier:
    def __init__(self, session=None):
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.session = session or requests.Session()
        self.api_base = f"https://api.telegram.org/bot{self.token}"
        
        # 外部回调
        self.on_refresh_dashboard = None 
        self.on_reset_stock = None 
        
        # 状态存储
        self.pending_input = {}       # 等待用户输入: {chat_id: action}
        self.active_menu_ids = {}     # 活跃的菜单ID: {chat_id: message_id} (用于实时刷新)
        # 记录管理员当前浏览的页码 {chat_id: page_number}
        self.admin_page_states = {}   

    def set_refresh_callback(self, callback):
        self.on_refresh_dashboard = callback

    def set_reset_callback(self, callback):
        self.on_reset_stock = callback

    # ================= 文件操作 =================
    def _load_products_file(self):
        if os.path.exists(PRODUCTS_FILE):
            try:
                with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return []
        return []

    def _save_products_file(self, data):
        try:
            with open(PRODUCTS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            print(f"保存 products.json 失败: {e}")
            return False
    
    # ================= 辅助逻辑 =================
    def _generate_product_list_text(self, page=1):
        """生成带序号的产品列表文本 (支持分页)"""
        products = self._load_products_file()
        total_items = len(products)
        total_pages = math.ceil(total_items / PAGE_SIZE)
        
        # 修正页码范围
        if page < 1: page = 1
        if page > total_pages and total_pages > 0: page = total_pages
        
        # 计算切片索引
        start_idx = (page - 1) * PAGE_SIZE
        end_idx = start_idx + PAGE_SIZE
        current_page_items = products[start_idx:end_idx]
        
        msg_lines = [f"📦 <b>当前监控分类列表 (第 {page}/{total_pages if total_pages > 0 else 1} 页)</b>\n"]
        
        if not products:
            msg_lines.append("<i>(暂无数据)</i>")
        else:
            for idx, item in enumerate(current_page_items, start=start_idx + 1):
                msg_lines.append(f"<b>{idx}. {item.get('name', '未命名')}</b>")
                msg_lines.append(f"   └ <code>{item.get('url', '无链接')}</code>")
        
        msg_lines.append(f"\n📊 共 {total_items} 个分类")
        msg_lines.append("👇 请选择操作 (支持多管理员实时同步):")
        
        full_text = "\n".join(msg_lines)
        return full_text, total_pages, page

    def _refresh_all_active_menus(self):
        if not self.active_menu_ids:
            return

        for chat_id, msg_id in list(self.active_menu_ids.items()):
            try:
                current_page = self.admin_page_states.get(chat_id, 1)
                new_text, total_pages, valid_page = self._generate_product_list_text(current_page)
                if valid_page != current_page:
                    self.admin_page_states[chat_id] = valid_page
                
                keyboard = self._get_product_mgmt_keyboard(valid_page, total_pages)
                self.edit_message(msg_id, new_text, chat_id, reply_markup=keyboard)
            except Exception as e:
                print(f"刷新管理员 {chat_id} 的面板失败: {e}")

    # ================= 消息发送底层方法 =================
    def send_message(self, text, chat_id=None, reply_markup=None):
        if not self.token: return None
        target_id = chat_id or self.chat_id
        if not target_id: return None

        try:
            url = f"{self.api_base}/sendMessage"
            payload = {
                "chat_id": target_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            if reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)
                
            resp = self.session.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"⚠️ 发送消息失败: {e}")
            return None

    def send_photo(self, photo_url_or_id, caption=None, chat_id=None, reply_markup=None):
        return self._send_photo_internal(photo_url_or_id, caption, chat_id, reply_markup)

    def send_image_bytes(self, image_bytes, caption=None, chat_id=None, reply_markup=None):
        if not self.token: return None
        target_id = chat_id or self.chat_id
        
        try:
            url = f"{self.api_base}/sendPhoto"
            data = {'chat_id': target_id}
            if caption:
                if len(caption) > 1024:
                    caption = caption[:1020] + "..."
                else:
                    data['parse_mode'] = 'HTML'
                data['caption'] = caption
                
            if reply_markup:
                data['reply_markup'] = json.dumps(reply_markup)

            files = {'photo': ('dashboard.png', image_bytes, 'image/png')}
            
            resp = self.session.post(url, data=data, files=files, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"⚠️ 发送图片(Bytes)失败: {e}")
            return None

    def _send_photo_internal(self, photo, caption, chat_id, reply_markup):
        if not self.token: return None
        if not photo:
            if caption: return self.send_message(caption, chat_id, reply_markup)
            return None

        target_id = chat_id or self.chat_id
        try:
            url = f"{self.api_base}/sendPhoto"
            payload = {"chat_id": target_id, "photo": photo, "parse_mode": "HTML"}
            if caption:
                if len(caption) > 1024:
                    caption = caption[:1020] + "..."
                    payload["parse_mode"] = None
                payload["caption"] = caption
            if reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)

            resp = self.session.post(url, json=payload, timeout=15)
            if resp.status_code == 400:
                if caption: return self.send_message(f"(图片无法显示)\n{caption}", chat_id, reply_markup)
            
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"⚠️ 发送图片异常: {e}")
            if caption: return self.send_message(f"(图片发送异常)\n{caption}", chat_id, reply_markup)
            return None

    def edit_message(self, message_id, text, chat_id=None, reply_markup=None):
        if not self.token: return False
        target_id = chat_id or self.chat_id

        try:
            url = f"{self.api_base}/editMessageText"
            payload = {
                "chat_id": target_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            if reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)

            resp = self.session.post(url, json=payload, timeout=10)
            if resp.status_code == 400 and "message is not modified" in resp.text:
                return True
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"⚠️ 编辑消息失败: {e}")
            return False

    def edit_photo(self, message_id, image_bytes, caption=None, chat_id=None, reply_markup=None):
        if not self.token: return False
        target_id = chat_id or self.chat_id
        try:
            url = f"{self.api_base}/editMessageMedia"
            media = {"type": "photo", "media": "attach://dashboard.png", "parse_mode": "HTML"}
            if caption: media["caption"] = caption
            data = {"chat_id": target_id, "message_id": message_id, "media": json.dumps(media)}
            if reply_markup: data["reply_markup"] = json.dumps(reply_markup)
            files = {'dashboard.png': ('dashboard.png', image_bytes, 'image/png')}
            resp = self.session.post(url, data=data, files=files, timeout=30)
            if resp.status_code == 400 and "message is not modified" in resp.text: return True
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"⚠️ 编辑图片失败: {e}")
            return False

    def delete_message(self, message_id, chat_id=None):
        if not self.token: return False
        target_id = chat_id or self.chat_id
        try:
            url = f"{self.api_base}/deleteMessage"
            payload = {"chat_id": target_id, "message_id": message_id}
            self.session.post(url, json=payload, timeout=10)
            return True
        except: return False

    # ================= 图片组支持 =================
    def send_media_group(self, image_data_list, caption=None, chat_id=None):
        if not self.token: return None
        target_id = chat_id or self.chat_id
        
        url = f"{self.api_base}/sendMediaGroup"
        files = {}
        media_payload = []
        
        safe_list = image_data_list[:10]
        
        for idx, (name, img_bytes) in enumerate(safe_list):
            file_key = f"photo{idx}"
            files[file_key] = (f"dashboard_{idx}.png", img_bytes, 'image/png')
            
            media_item = {
                "type": "photo",
                "media": f"attach://{file_key}" 
            }
            if idx == 0 and caption:
                media_item["caption"] = caption
                media_item["parse_mode"] = "HTML"
            media_payload.append(media_item)

        data = {
            "chat_id": target_id,
            "media": json.dumps(media_payload)
        }
        try:
            resp = self.session.post(url, data=data, files=files, timeout=60)
            resp.raise_for_status()
            return resp.json() 
        except Exception as e:
            print(f"⚠️ 发送 MediaGroup 失败: {e}")
            return None

    def edit_media_group_item(self, message_id, img_bytes, caption=None, chat_id=None):
        if not self.token: return False
        target_id = chat_id or self.chat_id
        url = f"{self.api_base}/editMessageMedia"
        files = {'new_photo': ('update.png', img_bytes, 'image/png')}
        media = {"type": "photo", "media": "attach://new_photo"}
        if caption:
            media["caption"] = caption
            media["parse_mode"] = "HTML"
        data = {
            "chat_id": target_id,
            "message_id": message_id,
            "media": json.dumps(media)
        }
        try:
            resp = self.session.post(url, data=data, files=files, timeout=30)
            if resp.status_code == 400 and "message is not modified" in resp.text: return True
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"⚠️ 编辑 MediaGroup 单图失败 (ID: {message_id}): {e}")
            return False

    # ================= 键盘布局 =================
    def _get_panel_keyboard(self):
        s = settings
        btn_service = "✅ 运行中" if s.get("enable_main_service") else "⛔️ 已暂停"
        btn_hourly = "✅" if s.get("enable_hourly_restock_info") else "❌"
        btn_pin = "✅" if s.get("auto_pin_dashboard") else "❌"
        btn_oos = "✅" if s.get("show_out_of_stock_list") else "❌"

        return {
            "inline_keyboard": [
                [{"text": f"🚀 主程序状态: {btn_service}", "callback_data": "toggle_service"}],
                [
                    {"text": f"定时: {btn_hourly}", "callback_data": "toggle_hourly"},
                    {"text": f"置顶: {btn_pin}", "callback_data": "toggle_pin"},
                    {"text": f"缺货: {btn_oos}", "callback_data": "toggle_oos"}
                ],
                [
                    {"text": "📦 货品列表", "callback_data": "manage_products"},
                    {"text": "📝 文案模板", "callback_data": "edit_template"},
                    {"text": "🖊 表头模板", "callback_data": "edit_header_template"}
                ],
                [{"text": "🔄 立即刷新看板", "callback_data": "force_refresh_dash"}],
                [{"text": "🗑 重置货物数据", "callback_data": "reset_stock_data"}]
            ]
        }

    def _get_product_mgmt_keyboard(self, current_page=1, total_pages=1):
        keyboard = [
            [{"text": "➕ 添加分类", "callback_data": "req_add_product"},
             {"text": "➖ 删除分类", "callback_data": "req_del_product"}],
        ]
        
        nav_row = []
        if current_page > 1:
            nav_row.append({"text": "⬅️ 上一页", "callback_data": f"flip_page:{current_page - 1}"})
        
        if current_page < total_pages:
            nav_row.append({"text": "下一页 ➡️", "callback_data": f"flip_page:{current_page + 1}"})
            
        if nav_row:
            keyboard.append(nav_row)
            
        keyboard.append([{"text": "🔙 返回主菜单", "callback_data": "back_to_panel"}])
        
        return {"inline_keyboard": keyboard}

    def answer_callback(self, callback_id, text=None, show_alert=False):
        if not self.token or not callback_id: return
        try:
            payload = {"callback_query_id": callback_id}
            if text:
                payload["text"] = text
                payload["show_alert"] = show_alert
            self.session.post(f"{self.api_base}/answerCallbackQuery", json=payload, timeout=5)
        except: pass

    # ================= 交互处理核心 =================
    def _handle_bot_callback(self, type, data):
        if type == "text":
            text = data.get('text', '').strip()
            chat_id = data.get('chat_id')
            
            # 1. 优先检查等待输入的动作
            pending_action = self.pending_input.get(chat_id)
            if pending_action:
                
                # --- 添加产品 ---
                if pending_action == "wait_add_product":
                    name = ""
                    url = ""
                    url_match = re.search(r'(https?://\S+)', text)
                    if url_match:
                        url = url_match.group(1)
                        name = text.replace(url, "").strip()
                    
                    if name and url:
                        products = self._load_products_file()
                        exists = any(p['url'] == url for p in products)
                        if exists:
                            self.send_message("⚠️ 该 URL 已存在于列表中。", chat_id)
                        else:
                            products.append({"name": name, "url": url})
                            if self._save_products_file(products):
                                self.send_message(f"✅ 已添加: {name}", chat_id)
                                self._refresh_all_active_menus()
                            else:
                                self.send_message("❌ 保存文件失败。", chat_id)
                    else:
                        self.send_message("⚠️ 格式错误。\n请回复: 名称 网址", chat_id)
                    
                    del self.pending_input[chat_id]
                    return

                # --- 删除产品 ---
                elif pending_action == "wait_del_product":
                    if text.isdigit():
                        idx = int(text)
                        products = self._load_products_file()
                        real_idx = idx - 1
                        
                        if 0 <= real_idx < len(products):
                            item = products.pop(real_idx)
                            if self._save_products_file(products):
                                self.send_message(f"✅ 已删除: {item['name']}", chat_id)
                                self._refresh_all_active_menus()
                                self.send_message("💡 Watcher 将在下次扫描时自动清理残留数据。", chat_id)
                            else:
                                self.send_message("❌ 保存失败。", chat_id)
                        else:
                            self.send_message(f"⚠️ 序号 {idx} 不存在。", chat_id)
                    else:
                        self.send_message("⚠️ 无效，请输入数字。", chat_id)
                        
                    del self.pending_input[chat_id]
                    return
                
                # --- 模板编辑 ---
                elif pending_action == "edit_dashboard_template":
                    settings.set("dashboard_caption_template", text)
                    self.send_message(f"✅ 看板文案模板已更新！", chat_id)
                    del self.pending_input[chat_id]
                    return
                elif pending_action == "edit_header_template":
                    settings.set("dashboard_header_template", text)
                    self.send_message(f"✅ 表头模板已更新！", chat_id)
                    del self.pending_input[chat_id]
                    return
            
            if text == "/panel":
                if str(chat_id) not in ADMIN_USER_IDS:
                    self.send_message("⛔️ 无权访问", chat_id)
                    return
                self.send_message("⚙️ <b>华盛监控控制台</b>", chat_id, reply_markup=self._get_panel_keyboard())

        elif type == "callback":
            cb_data = data.get('data')
            chat_id = data.get('chat_id')
            msg_id = data.get('message_id')
            callback_id = data.get('callback_id')
            
            if str(chat_id) not in ADMIN_USER_IDS:
                self.answer_callback(callback_id, "⛔️ 无权操作", show_alert=True)
                return

            if cb_data == "toggle_service":
                curr = settings.get("enable_main_service")
                settings.set("enable_main_service", not curr)
                self.edit_message(msg_id, "⚙️ <b>华盛监控控制台</b>", chat_id, reply_markup=self._get_panel_keyboard())
                status_text = "暂停" if curr else "启动"
                self.answer_callback(callback_id, f"✅ 主服务已{status_text}")

            elif cb_data == "manage_products":
                self.admin_page_states[chat_id] = 1
                full_text, total_pages, _ = self._generate_product_list_text(1)
                self.edit_message(msg_id, full_text, chat_id, reply_markup=self._get_product_mgmt_keyboard(1, total_pages))
                self.active_menu_ids[chat_id] = msg_id
                self.answer_callback(callback_id)

            elif cb_data.startswith("flip_page:"):
                try:
                    target_page = int(cb_data.split(":")[1])
                    self.admin_page_states[chat_id] = target_page
                    
                    full_text, total_pages, valid_page = self._generate_product_list_text(target_page)
                    self.edit_message(msg_id, full_text, chat_id, reply_markup=self._get_product_mgmt_keyboard(valid_page, total_pages))
                    self.answer_callback(callback_id)
                except Exception as e:
                    print(f"翻页错误: {e}")

            elif cb_data == "req_add_product":
                self.answer_callback(callback_id)
                self.pending_input[chat_id] = "wait_add_product"
                self.send_message("➕ 请回复：<code>分类名称 网址链接</code>", chat_id)

            elif cb_data == "req_del_product":
                self.answer_callback(callback_id)
                self.pending_input[chat_id] = "wait_del_product"
                self.send_message("➖ 请回复要删除的 <b>数字序号</b> :", chat_id)

            elif cb_data == "back_to_panel":
                if chat_id in self.pending_input:
                    del self.pending_input[chat_id]
                self.edit_message(msg_id, "⚙️ <b>华盛监控控制台</b>", chat_id, reply_markup=self._get_panel_keyboard())
                if chat_id in self.active_menu_ids:
                    del self.active_menu_ids[chat_id]
                if chat_id in self.admin_page_states:
                    del self.admin_page_states[chat_id]
                self.answer_callback(callback_id)

            elif cb_data == "force_refresh_dash":
                if self.on_refresh_dashboard: self.on_refresh_dashboard()
                self.answer_callback(callback_id, "✅ 已触发刷新", show_alert=False)

            elif cb_data == "reset_stock_data":
                if self.on_reset_stock: self.on_reset_stock()
                self.answer_callback(callback_id, "⚠️ 数据已重置", show_alert=True)
                
            elif cb_data == "toggle_hourly":
                settings.set("enable_hourly_restock_info", not settings.get("enable_hourly_restock_info"))
                self.edit_message(msg_id, "⚙️ <b>华盛监控控制台</b>", chat_id, reply_markup=self._get_panel_keyboard())
                self.answer_callback(callback_id, "✅ 设置已更新")
            
            elif cb_data == "toggle_pin":
                settings.set("auto_pin_dashboard", not settings.get("auto_pin_dashboard"))
                self.edit_message(msg_id, "⚙️ <b>华盛监控控制台</b>", chat_id, reply_markup=self._get_panel_keyboard())
                self.answer_callback(callback_id, "✅ 设置已更新")
                
            elif cb_data == "toggle_oos":
                settings.set("show_out_of_stock_list", not settings.get("show_out_of_stock_list"))
                self.edit_message(msg_id, "⚙️ <b>华盛监控控制台</b>", chat_id, reply_markup=self._get_panel_keyboard())
                self.answer_callback(callback_id, "✅ 设置已更新")

            elif cb_data == "edit_template":
                self.answer_callback(callback_id)
                self.pending_input[chat_id] = "edit_dashboard_template"
                tpl = settings.get("dashboard_caption_template")
                self.send_message(f"📝 请回复新的看板模板:\n当前: <code>{tpl}</code>", chat_id)

            elif cb_data == "edit_header_template":
                self.answer_callback(callback_id)
                self.pending_input[chat_id] = "edit_header_template"
                tpl = settings.get("dashboard_header_template")
                self.send_message(f"🖊 请回复新的表头模板:\n当前: <code>{tpl}</code>", chat_id)

    def start_polling(self):
        if not self.token: return
        print("🤖 Telegram 机器人监听中...")
        def _poll_loop():
            offset = 0
            url = f"{self.api_base}/getUpdates"
            while True:
                try:
                    resp = self.session.get(url, params={"offset": offset + 1, "timeout": 60}, timeout=70)
                    if resp.status_code == 200:
                        result = resp.json().get("result", [])
                        for update in result:
                            offset = update["update_id"]
                            
                            # 消息或频道广播
                            if "message" in update or "channel_post" in update:
                                message = update.get("message") or update.get("channel_post")
                                if message:
                                    # [新增] 自动删除置顶服务消息
                                    if "pinned_message" in message:
                                        print(f"🧹 检测到置顶通知，正在删除 (MsgID: {message['message_id']})")
                                        self.delete_message(message['message_id'], message['chat']['id'])
                                        continue # 跳过后续处理

                                    self._handle_bot_callback("text", {
                                        "text": message.get("text", ""),
                                        "chat_id": message["chat"]["id"]
                                    })

                            # 按钮回调
                            elif "callback_query" in update:
                                callback = update["callback_query"]
                                callback_id = callback["id"]
                                data = callback["data"]
                                chat_id = callback["message"]["chat"]["id"]
                                message_id = callback["message"]["message_id"]
                                
                                self._handle_bot_callback("callback", {
                                    "data": data,
                                    "chat_id": chat_id,
                                    "message_id": message_id,
                                    "callback_id": callback_id
                                })

                except Exception as e:
                    print(f"⚠️ Telegram 监听异常: {e}")
                    time.sleep(5)
                time.sleep(0.5)

        t = threading.Thread(target=_poll_loop, daemon=True)
        t.start()