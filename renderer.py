from PIL import Image, ImageDraw, ImageFont
import io
import datetime
import os
import sys

# 尝试导入 settings 以获取动态配置
try:
    from settings import settings
except ImportError:
    settings = None

class DashboardRenderer:
    def __init__(self):
        # ================= 字体加载逻辑 =================
        # 依然优先尝试加载粗体文件，这样不用描边也能显眼
        font_candidates = [
            "C:/Windows/Fonts/msyhbd.ttc",                           # 优先尝试微软雅黑粗体
            "C:/Windows/Fonts/msyh.ttc",                             # Windows 微软雅黑
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",   # Linux Noto Bold
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc" # Linux Noto Regular
        ]
        
        self.font_path = None
        for path in font_candidates:
            if os.path.exists(path):
                self.font_path = path
                break
        
        if self.font_path:
            print(f"✅ [Renderer] 已加载字体: {self.font_path}")
            # 保持大字号，清晰度拉满
            self.title_font = ImageFont.truetype(self.font_path, 80)  # 主标题
            self.header_font = ImageFont.truetype(self.font_path, 45) # 副标题/统计
            self.text_font = ImageFont.truetype(self.font_path, 38)   # 分类标题
            self.small_font = ImageFont.truetype(self.font_path, 28)  # 商品名称
        else:
            print("⚠️ 未找到指定字体，使用默认字体 (中文可能不显示)")
            default = ImageFont.load_default()
            self.title_font = default
            self.header_font = default
            self.text_font = default
            self.small_font = default

        # ================= 颜色定义：烟草色系 =================
        self.colors = {
            "bg": "#f5f5f5",          # 全局背景
            "card_bg": "#ffffff",     # 卡片背景
            "text": "#212121",        # 字体颜色(深黑灰)
            "green": "#2e7d32",       # 有货绿
            "red": "#c62828",         # 缺货红
            "gray": "#757575",        # 辅助灰
            
            # --- 烟草风格 ---
            "header_bg": "#EFEBE9",       # 头部大背景
            "section_bg": "#D7CCC8",      # 分类框背景
            "section_border": "#5D4037",  # 分类框边框
            "title_text": "#3E2723",      # 标题深褐
            "oos_bg": "#ffebee",
            "oos_border": "#ef5350"
        }

    def render_group(self, items, full_in_stock=None, full_out_stock=None):
        """
        [新接口] 生成图片组 (按大类前缀分组)
        返回格式: [(category_prefix, image_bytes), ...]
        """
        # 1. 按“大类前缀”分组
        grouped_by_prefix = self._group_by_prefix(items)
        
        if full_in_stock is None:
            full_in_stock = len([i for i in items if not i['is_sold_out']])
        if full_out_stock is None:
            full_out_stock = len([i for i in items if i['is_sold_out']])

        # 2. 预加载 Banner (以 2000 宽加载)
        banner_img = self._load_banner(total_width=2000)
        
        images_list = []
        
        if not grouped_by_prefix:
            img_bytes = self._draw_single_canvas("总览", [], banner_img, full_in_stock, full_out_stock, is_empty=True)
            images_list.append(("总览", img_bytes))
        else:
            for prefix, prods in grouped_by_prefix.items():
                prods.sort(key=lambda x: x['is_sold_out'])
                img_bytes = self._draw_single_canvas(prefix, prods, banner_img, full_in_stock, full_out_stock)
                images_list.append((prefix, img_bytes))
                
        return images_list

    def _load_banner(self, total_width):
        banner_files = ["banner.png", "banner.jpg", "banner.jpeg"]
        for f in banner_files:
            if os.path.exists(f):
                try:
                    raw = Image.open(f).convert("RGBA")
                    if raw.width != total_width:
                        ratio = total_width / raw.width
                        new_h = int(raw.height * ratio)
                        return raw.resize((total_width, new_h), Image.Resampling.LANCZOS)
                    return raw
                except Exception as e:
                    print(f"⚠️ Banner加载失败: {e}")
        return None

    def _draw_single_canvas(self, prefix_name, prods, banner_img, in_stock, out_stock, is_empty=False):
        """
        绘制单个大类的图片 (高清版)
        """
        padding = 40  
        total_width = 2000 
        
        # 头部高度计算
        banner_h = banner_img.height if banner_img else 40
        title_area_h = 200 
        header_total_h = banner_h + title_area_h

        # 布局参数
        row_height = 70       
        sub_cat_header_height = 60  
        num_cols = 4
        col_gap = 30          
        col_width = (total_width - (padding * 2) - (col_gap * (num_cols - 1))) / num_cols
        
        # --- 1. 内部按小类分组 ---
        sub_grouped = {}
        if not is_empty:
            for p in prods:
                full_name = p.get('category', '')
                if '-' in full_name:
                    sub_name = full_name.split('-', 1)[1].strip()
                else:
                    sub_name = full_name if full_name != prefix_name else "其他"
                
                if sub_name not in sub_grouped: sub_grouped[sub_name] = []
                sub_grouped[sub_name].append(p)

            for k in sub_grouped:
                sub_grouped[k].sort(key=lambda x: x['is_sold_out'])

        # --- 2. 瀑布流布局计算 ---
        cols_height = [0] * num_cols
        layout_map = [] 

        if not is_empty:
            for sub_cat, items in sub_grouped.items():
                rows = len(items)
                block_h = sub_cat_header_height + (rows * (row_height + 5)) + 40 
                
                min_h = min(cols_height)
                col_idx = cols_height.index(min_h)
                
                layout_map.append({
                    "sub_cat": sub_cat,
                    "prods": items,
                    "col_idx": col_idx,
                    "y": min_h,
                    "h": block_h
                })
                cols_height[col_idx] += block_h

        content_height = max(cols_height) if cols_height else 100
        prefix_title_height = 100
        
        total_canvas_h = header_total_h + padding + prefix_title_height + content_height + 80

        # --- 3. 开始绘图 ---
        im = Image.new('RGB', (total_width, int(total_canvas_h)), self.colors['bg'])
        draw = ImageDraw.Draw(im)

        # 绘制头部背景
        draw.rectangle([(0, 0), (total_width, header_total_h)], fill=self.colors['header_bg'])

        # 绘制 Banner
        if banner_img:
            paste_x = (total_width - banner_img.width) // 2
            im.paste(banner_img, (paste_x, 0), banner_img)

        # 绘制头部统计文字
        self._draw_header_text(draw, banner_h, total_width, in_stock, out_stock)

        # 内容起始 Y
        base_y = header_total_h + padding
        
        if is_empty:
            self._draw_centered_text(draw, "暂无商品数据", self.header_font, base_y + 80, total_width, self.colors['gray'])
        else:
            # A. 绘制大类标题
            icon = "■"
            main_title = f"{icon} {prefix_name}系列"
            # [修改] 移除了 stroke_width，回归清爽
            self._draw_centered_text(draw, main_title, self.header_font, base_y, total_width, self.colors['title_text'])
            
            # B. 绘制各个小类块
            blocks_start_y = base_y + prefix_title_height
            
            for block in layout_map:
                abs_x = padding + (block['col_idx'] * (col_width + col_gap))
                abs_y = blocks_start_y + block['y']
                
                self._draw_sub_category_block(
                    draw, abs_x, abs_y, col_width, row_height,
                    block['sub_cat'], block['prods']
                )

        output = io.BytesIO()
        im.save(output, format='PNG')
        return output.getvalue()

    def _draw_sub_category_block(self, draw, x, y, width, row_height, sub_cat, prods):
        """绘制一个小类块"""
        bg_col = self.colors['section_bg']
        bd_col = self.colors['section_border']
        
        # 1. 小类标题条
        header_h = 50 
        draw.rectangle([x, y, x + width, y + header_h], fill=bg_col, outline=bd_col, width=0)
        
        # 标题文字 [修改] 移除了 stroke_width
        draw.text((x + 15, y + 5), sub_cat, font=self.text_font, fill=self.colors['title_text'])
        
        # 2. 商品列表
        curr_y = y + header_h + 8
        inner_padding = 5
        item_w = width - (inner_padding * 2)
        item_x = x + inner_padding
        
        for p in prods:
            self._draw_item(draw, item_x, curr_y, item_w, row_height, p)
            curr_y += row_height + 5

    def _draw_header_text(self, draw, text_start_y, total_width, in_s, out_s):
        header_tpl = "华盛烟丝库存看板"
        if settings:
             val = settings.get("dashboard_header_template")
             if val: header_tpl = val
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        try:
            full_text = header_tpl.format(
                site_name="华盛烟丝库存看板", 
                update_time=timestamp, 
                in_stock_count=in_s, 
                out_stock_count=out_s
            )
        except:
            full_text = f"华盛烟丝库存看板\n{timestamp}"
        
        import re
        lines = re.sub(r'<.*?>', '', full_text).split('\n')
        title = lines[0] if lines else "华盛烟丝库存看板"
        subtitle = lines[1] if len(lines) > 1 else ""
        if len(lines)>2: subtitle += " | " + " ".join(lines[2:])
        
        # [修改] 移除了 stroke_width
        self._draw_centered_text(draw, title, self.title_font, text_start_y + 30, total_width, self.colors['title_text'])
        self._draw_centered_text(draw, subtitle, self.header_font, text_start_y + 130, total_width, self.colors['text'])

    def _group_by_prefix(self, items):
        """按大类前缀分组"""
        grouped = {}
        for item in items:
            full_cat_name = item.get('category', '其他')
            if '-' in full_cat_name:
                prefix = full_cat_name.split('-')[0].strip()
            else:
                prefix = full_cat_name
            
            if prefix not in grouped: grouped[prefix] = []
            grouped[prefix].append(item)
        return grouped

    def _draw_centered_text(self, draw, text, font, y, total_width, fill):
        if self.font_path:
             _, _, w, h = draw.textbbox((0, 0), text, font=font)
             x = (total_width - w) / 2
        else: x = 20
        draw.text((x, y), text, font=font, fill=fill)

    def _draw_item(self, draw, x, y, w, h, item):
        is_stock = not item['is_sold_out']
        
        # 卡片背景
        draw.rectangle([x, y, x + w, y + h], fill=self.colors['card_bg'], outline="#bdbdbd", width=1)
        
        # 状态点
        dot_color = self.colors['green'] if is_stock else self.colors['red']
        draw.ellipse([x + 15, y + (h-16)/2, x + 31, y + (h-16)/2 + 16], fill=dot_color)
        
        # 商品名称
        name = item['name']
        max_chars = int((w - 50) / 22) 
        if len(name) > max_chars: name = name[:max_chars-1] + ".."
        
        # [修改] 移除了 stroke_width，文字更干净
        draw.text((x + 45, y + (h-30)/2), name, font=self.small_font, fill=self.colors['text'])

renderer = DashboardRenderer()