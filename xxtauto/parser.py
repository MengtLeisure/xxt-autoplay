# -*- coding: utf-8 -*-
"""学习通“学生学习页面”的图像识别解析器。

输入：一张窗口截图 + 一次 OCR 结果
输出：PageState（章节目录、当前高亮、播放器进度、按钮、弹窗、测验）

全部逻辑只依赖像素与 OCR 文字，不读取网页 DOM。
"""
from __future__ import annotations

import math
import re
import time
from collections import deque
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .config import Config
from .model import (Button, PageState, PlayState, PopupInfo, Rect, SidebarItem,
                    VideoPanel)
from .ocr import OcrDocument, OcrLine
from .screen import Frame, color_close, luminance, longest_true_run, sample_color

# 识别弹窗按钮的关键词（越靠前优先级越高）
POPUP_BUTTON_WORDS = ["确定", "知道了", "我知道了", "继续观看", "继续学习", "继续播放",
                      "关闭", "取消", "好的", "跳过", "稍后再说"]
POPUP_NOTICE_WORDS = ["已看完", "本次学习已完成", "视频已播放完毕", "提示", "系统消息",
                      "网络异常", "加载失败", "重试", "异常", "错误", "重新播放"]
QUIZ_WORDS = ["单选题", "多选题", "判断题", "填空题", "答题卡", "提交答案", "上一题",
              "下一题", "我的答案", "正确答案", "章节测验", "测验"]


def _seconds(text: str) -> int:
    parts = [int(p) for p in text.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] if parts else 0


class XxtPageParser:
    """把「截图 + OCR」解析成页面状态。"""

    def __init__(self, config: Config, logger=None):
        self.cfg = config
        self.logger = logger
        self._time_re = re.compile(r"(\d{1,2}(?::\d{2}){1,2})\s*/\s*(\d{1,2}(?::\d{2}){1,2})")
        # OCR 有时会把 “0:04 / 12:13” 识别成 “04/12:13”，这里放宽一个冒号
        self._time_loose_re = re.compile(r"(\d{1,2}(?::\d{2}){0,2})\s*[/／]\s*(\d{1,2}(?::\d{2}){1,2})")
        self._chapter_re = re.compile(config.chapter_number_re)
        self._section_re = re.compile(config.section_number_re)
        self._last_icon_center: Optional[Tuple[int, int]] = None

    # ------------------------------------------------------------------ 入口
    def parse(self, frame: Frame, doc: OcrDocument,
              previous: Optional[PageState] = None) -> PageState:
        state = PageState(doc=doc, timestamp=time.time())
        W, H = frame.width, frame.height
        state.sidebar_right = W

        tabs = self._find_tabs(doc, H)
        state.is_xxt_page = (len(tabs) >= 2) or self._has_marker(doc)
        state.sidebar_left = self._sidebar_left(tabs, W, doc)
        content_right = max(10, state.sidebar_left - 8)

        state.chapter_title = self._find_chapter_title(doc, content_right, H, tabs)
        state.items = self._parse_sidebar(frame, doc, tabs, H)
        state.active_item = self._detect_active(frame, doc, state.items, tabs, H)
        if state.active_item is None and state.chapter_title:
            state.active_item = self._match_title(state.items, state.chapter_title)

        state.video = self._parse_video(frame, doc, content_right, H)
        if state.video.video_rect is None and previous is not None:
            # 画面较亮时可能测不到视频边界，沿用上一次的识别结果
            state.video.video_rect = previous.video.video_rect
            state.video.bar_rect = previous.video.bar_rect
            state.video.bar_row_y = previous.video.bar_row_y
            state.video.hover_point = previous.video.hover_point
            state.video.icon_center = previous.video.icon_center
        state.next_button = self._find_next_button(frame, doc, content_right, H)
        state.popup = self._detect_popup(doc, content_right, H, tabs)
        state.quiz = self._detect_quiz(doc, content_right, H, tabs)
        state.layout_ready = bool(state.is_xxt_page and (state.items or state.chapter_title))
        return state

    # ---------------------------------------------------------------- 快速帧
    def refresh_video(self, frame: Frame, state: PageState) -> Optional[float]:
        """不重新 OCR，只根据像素刷新进度条填充比例/播放状态。"""
        vp = state.video
        x0, x1 = self._content_span(frame, state)
        row_y = vp.bar_row_y
        if row_y:
            candidates = range(max(0, row_y - 7), min(frame.height, row_y + 8))
        else:
            # 控制条还没出现过：在视频区域里大范围找一次
            candidates = range(int(frame.height * 0.35), int(frame.height * 0.95))
        new_row = self._detect_bar_row(frame, x0, x1, candidates)
        if new_row is None:
            # 视频还没开始：可能是整块黑色 + 中央大播放按钮
            if vp.video_rect is None or vp.bar_row_y == 0:
                black = self.find_black_video_rect(frame, max(10, state.sidebar_left - 8))
                if black is not None:
                    vp.video_rect = black
                    vp.hover_point = (black.cx, black.cy)
                vp.center_play = self.detect_center_play_button(
                    frame, max(10, state.sidebar_left - 8))
            return vp.bar_fill
        span = self._detect_video_span(frame, new_row, max(10, state.sidebar_left - 8))
        if span is not None:
            vp.video_rect = Rect(span[0], max(0, new_row - 520), span[1],
                                 min(frame.height, new_row + 60))
        bar = self._bar_rect(vp.video_rect, new_row, x0, x1)
        vp.bar_row_y = new_row
        vp.bar_rect = bar
        vp.bar_fill = self._bar_fill(frame, new_row, bar)
        vp.play_state = self._detect_play_state(frame, bar)
        vp.icon_center = self._last_icon_center or (bar.x0 + 30, bar.y1 + 46)
        vp.center_play = False
        if vp.hover_point is None:
            vp.hover_point = (bar.x0 + int(bar.width * 0.35), max(10, new_row - 130))
        return vp.bar_fill

    @staticmethod
    def _content_span(frame: Frame, state: PageState):
        vp = state.video
        if vp.video_rect is not None:
            return vp.video_rect.x0, vp.video_rect.x1
        x0 = int(frame.width * 0.03)
        x1 = max(x0 + 100, state.sidebar_left - 10)
        return x0, x1

    # ------------------------------------------------------------ 页面骨架
    def _find_tabs(self, doc: OcrDocument, H: int) -> List[OcrLine]:
        words = set(self.cfg.sidebar_tab_words)
        tabs = [ln for ln in doc.lines if ln.text in words and ln.cy < H * 0.35]
        return tabs

    def _has_marker(self, doc: OcrDocument) -> bool:
        text = doc.text()
        return any(w in text for w in self.cfg.page_marker_words)

    def _sidebar_left(self, tabs: Sequence[OcrLine], W: int, doc: OcrDocument) -> int:
        if tabs:
            left = min(ln.x0 for ln in tabs) - 45
            return max(int(W * 0.5), min(W - 120, left))
        # 退路：根据屏幕右侧“目录/讨论”等文字或固定比例
        candidate = [ln for ln in doc.lines if ln.x0 > W * 0.68]
        if candidate:
            return max(int(W * 0.5), min(W - 120, int(W * 0.72)))
        return int(W * 0.72)

    def _find_chapter_title(self, doc: OcrDocument, content_right: int, H: int,
                            tabs: Sequence[OcrLine]) -> str:
        tab_y = max((ln.y1 for ln in tabs), default=int(H * 0.25))
        candidates = [ln for ln in doc.lines
                      if ln.cx < content_right and ln.height >= 23
                      and tab_y < ln.y0 < H * 0.45 and len(ln.text) >= 2]
        if not candidates:
            return ""
        candidates.sort(key=lambda ln: (-ln.height, ln.y0))
        return candidates[0].text

    # ---------------------------------------------------------------- 目录
    def _parse_sidebar(self, frame: Frame, doc: OcrDocument,
                       tabs: Sequence[OcrLine], H: int) -> List[SidebarItem]:
        if not tabs:
            return []
        tab_y1 = max(ln.y1 for ln in tabs)
        left = self._sidebar_left(tabs, frame.width, doc)
        items: List[SidebarItem] = []
        section = ""
        for ln in sorted(doc.lines, key=lambda x: x.y0):
            if ln.cy <= tab_y1 + 8 or ln.x0 < left - 25:
                continue
            m = self._chapter_re.match(ln.text)
            if m:
                number = f"{m.group(1)}.{m.group(2)}"
                title = m.group(3).strip(" .·-—")
                items.append(SidebarItem(number=number, title=title, line=ln, section=section))
                continue
            sm = self._section_re.match(ln.text)
            if sm and len(ln.text) <= 24:
                section = ln.text
        return items

    def _detect_active(self, frame: Frame, doc: OcrDocument,
                       items: Sequence[SidebarItem], tabs: Sequence[OcrLine],
                       H: int) -> Optional[SidebarItem]:
        if not items:
            return None
        target = tuple(self.cfg.active_bg_color)
        tol = int(self.cfg.active_bg_tolerance)
        active: List[SidebarItem] = []
        for it in items:
            left = max(0, it.line.x0 - 26)
            color = sample_color(frame.pixels, left, it.line.cy)
            if color_close(color, target, tol) and color[2] >= target[2] - 3:
                it.active = True
                active.append(it)
        if len(active) == 1:
            return active[0]
        if len(active) > 1:
            # 多个候选时取最靠上的（通常当前高亮行唯一）
            return active[0]
        return None

    def _match_title(self, items: Sequence[SidebarItem], title: str) -> Optional[SidebarItem]:
        def norm(s: str) -> str:
            return re.sub(r"[\s（）()【】\[\]…\.。、,，:：\-—_]+", "", s or "")
        t = norm(title)
        if not t:
            return None
        best, best_score = None, 0.0
        for it in items:
            it_norm = norm(it.title)
            if not it_norm:
                continue
            if it_norm == t:
                return it
            if it_norm in t or t in it_norm:
                score = min(len(it_norm), len(t)) / max(len(it_norm), len(t))
            else:
                common = sum(1 for a, b in zip(it_norm, t) if a == b)
                score = common / max(len(it_norm), len(t))
            if score > best_score:
                best, best_score = it, score
        if best is not None and best_score >= 0.55:
            return best
        return None

    # -------------------------------------------------------------- 播放器
    def _parse_video(self, frame: Frame, doc: OcrDocument,
                     content_right: int, H: int) -> VideoPanel:
        vp = VideoPanel()
        W = frame.width
        # 1) 找播放器时间文字 “0:04 / 12:13”
        time_line: Optional[OcrLine] = None
        time_match = None
        for ln in doc.lines:
            if ln.cx >= content_right or ln.y0 < H * 0.35 or len(ln.text) > 40:
                continue
            m = self._time_re.search(ln.text)
            if m is None:
                m = self._time_loose_re.search(ln.text)
                if m is not None and ":" not in m.group(2):
                    m = None
            if m is not None:
                time_line = ln
                time_match = m
                break
        if time_line is not None and time_match is not None:
            try:
                vp.time_current = self._to_seconds(time_match.group(1))
                vp.time_total = self._to_seconds(time_match.group(2))
            except Exception:
                pass
            vp.bar_search_y = time_line.y0

        x0 = int(W * 0.03)
        x1 = max(x0 + 100, content_right - 10)
        if time_line is not None:
            candidates = range(max(0, time_line.y0 - 95), max(1, time_line.y0 - 5))
        else:
            candidates = range(int(H * 0.55), int(H * 0.93))

        # 2) 找进度条所在行
        row_y = self._detect_bar_row(frame, x0, x1, candidates)
        if row_y is not None:
            # 3) 在该行上找视频画面的左右边界（非纯白像素的连续段）
            span = self._detect_video_span(frame, row_y, content_right)
            if span is not None:
                vp.video_rect = Rect(span[0], max(0, row_y - 520), span[1], min(H, row_y + 60))
            bar = self._bar_rect(vp.video_rect, row_y, x0, x1)
            vp.bar_row_y = row_y
            vp.bar_rect = bar
            vp.bar_fill = self._bar_fill(frame, row_y, bar)
            vp.play_state = self._detect_play_state(frame, bar)
            vp.icon_center = self._last_icon_center or (bar.x0 + 30, bar.y1 + 46)
            vp.hover_point = (bar.x0 + int(bar.width * 0.35), max(10, row_y - 130))
        if vp.hover_point is None:
            if vp.video_rect is not None:
                vp.hover_point = (vp.video_rect.cx, vp.video_rect.y0 + int(vp.video_rect.height * 0.6))
            else:
                vp.hover_point = (max(20, content_right // 2), int(H * 0.55))
        # 视频还没开始时，画面是整块黑色，中央有一个灰色大播放按钮
        if vp.video_rect is None:
            black = self.find_black_video_rect(frame, content_right)
            if black is not None:
                vp.video_rect = black
                vp.hover_point = (black.cx, black.cy)
        vp.center_play = self.detect_center_play_button(frame, content_right)
        return vp

    # ---------------------------------------------------------- 未开始播放
    def find_black_video_rect(self, frame: Frame, content_right: int) -> Optional[Rect]:
        """找整块黑色的视频区域（视频尚未开始播放时的画面）。"""
        lum = luminance(frame.pixels)
        content = lum[:, :content_right]
        if content.size == 0:
            return None
        dark_rows = (content < 30).mean(axis=1) > 0.5
        start, end, _ = longest_true_run(dark_rows, max_gap=6)
        if end - start < frame.height * 0.22:
            return None
        band = content[start:end + 1]
        dark_cols = (band < 30).mean(axis=0) > 0.5
        cstart, cend, _ = longest_true_run(dark_cols, max_gap=24)
        if cend - cstart < content_right * 0.35:
            return None
        return Rect(cstart, start, cend, end)

    def detect_center_play_button(self, frame: Frame, content_right: int) -> bool:
        """判断视频中央是否显示着“大播放按钮”（灰色圆盘 + 白色三角）。"""
        black = self.find_black_video_rect(frame, content_right)
        if black is None:
            return False
        cx, cy = black.cx, black.cy
        half = 100
        x0 = max(0, cx - half)
        x1 = min(frame.width, cx + half)
        y0 = max(0, cy - half)
        y1 = min(frame.height, cy + half)
        if x1 - x0 < 60 or y1 - y0 < 60:
            return False
        region = frame.pixels[y0:y1, x0:x1]
        mx = region.max(axis=2)
        mn = region.min(axis=2)
        mean = region.mean(axis=2)
        gray = ((mx - mn) < 32) & (mean > 80) & (mean < 190)
        return float(gray.mean()) >= 0.10

    @staticmethod
    def _to_seconds(token: str) -> int:
        token = (token or "").strip()
        if ":" in token:
            return _seconds(token)
        if len(token) == 3:
            return int(token[0]) * 60 + int(token[1:])
        return int(token)

    def _detect_video_span(self, frame: Frame, row_y: int,
                           content_right: int) -> Optional[Tuple[int, int]]:
        """在进度条上下若干行里估计视频画面的左右边界，取多行的中位数。

        只看进度条那一行容易受“白色把手/时间文字”影响，因此向上多取几行。
        """
        starts: List[int] = []
        ends: List[int] = []
        for dy in (0, -2, -4, -6, -8, 2, 4):
            y = row_y + dy
            if y <= 0 or y >= frame.height:
                continue
            row = frame.pixels[y, :content_right]
            if row.size < 100:
                continue
            page_white = row.min(axis=1) > 235
            non_white = ~page_white
            start, end, _ = longest_true_run(non_white, max_gap=45)
            if end - start < content_right * 0.35:
                continue
            if start < content_right * 0.02:
                continue  # 避免把左侧工具栏连进来
            starts.append(start)
            ends.append(end)
        if len(starts) < 2:
            return None
        starts.sort()
        ends.sort()
        mid = len(starts) // 2
        return starts[mid], ends[mid]

    def _detect_bar_row(self, frame: Frame, x0: int, x1: int,
                        y_candidates: Sequence[int]) -> Optional[int]:
        """在候选行中找“细亮的横线”，即播放器进度条所在行。"""
        if x1 - x0 < 80:
            return None
        H = frame.height
        lum = luminance(frame.pixels)
        med = np.median(lum[:, x0:x1], axis=1)
        best_y, best_score = -1, 0.0
        for y in y_candidates:
            if y <= 3 or y >= H - 3:
                continue
            center = float(med[y])
            up = float(np.min(med[max(0, y - 5):y]))
            down = float(np.min(med[y + 1:min(H, y + 6)]))
            isolated = center - max(up, down)
            if center >= 40 and isolated >= 12:
                score = center + isolated
                if score > best_score:
                    best_y, best_score = y, score
        return best_y if best_y >= 0 else None

    @staticmethod
    def _bar_rect(video_rect: Optional[Rect], row_y: int, x0: int, x1: int) -> Rect:
        if video_rect is not None:
            margin = 28
            left = max(0, video_rect.x0 + margin)
            right = max(left + 40, video_rect.x1 - margin)
            return Rect(left, row_y - 1, right, row_y + 2)
        return Rect(x0, row_y - 1, x1, row_y + 2)

    def _bar_fill(self, frame: Frame, row_y: int, bar: Rect) -> Optional[float]:
        """蓝色已播放部分在进度条上的占比。"""
        left = max(0, bar.x0)
        right = min(frame.width, bar.x1 + 1)
        if right - left < 20:
            return None
        row = frame.pixels[row_y, left:right]
        r = row[:, 0]
        b = row[:, 2]
        blue = (b > r + 55) & (b > 140)
        return max(0.0, min(1.0, float(np.count_nonzero(blue)) / float(right - left)))

    def _detect_play_state(self, frame: Frame, bar: Rect) -> str:
        """控制条左侧的按钮形状：两条竖条=正在播放；三角形=已暂停。"""
        self._last_icon_center = None
        x0 = bar.x0 + 6
        x1 = min(frame.width - 1, bar.x0 + 70)
        y0 = min(frame.height - 2, bar.y1 + 8)
        y1 = min(frame.height - 1, bar.y1 + 74)
        if x1 - x0 < 12 or y1 - y0 < 12:
            return PlayState.UNKNOWN
        region = frame.pixels[y0:y1, x0:x1]
        bright = (region[:, :, 0] > 185) & (region[:, :, 1] > 185) & (region[:, :, 2] > 185)
        components = self._components(bright, min_area=14)
        if not components:
            return PlayState.UNKNOWN
        components.sort(key=lambda b: b[2] * b[3], reverse=True)
        big = [b for b in components if b[2] >= 4 and b[3] >= 8]
        if len(big) >= 2:
            # 暂停图标是两条竖条，点击区域取两者中间
            b1, b2 = big[0], big[1]
            cx = (b1[0] + b1[2] / 2 + b2[0] + b2[2] / 2) / 2
            cy = (b1[1] + b1[3] / 2 + b2[1] + b2[3] / 2) / 2
            self._last_icon_center = (int(x0 + cx), int(y0 + cy))
            return PlayState.PLAYING
        if len(big) == 1:
            w, h = big[0][2], big[0][3]
            self._last_icon_center = (x0 + big[0][0] + w // 2, y0 + big[0][1] + h // 2)
            if w >= 7 and 0.8 <= (h / max(1, w)) <= 2.6:
                return PlayState.PAUSED
        return PlayState.UNKNOWN

    @staticmethod
    def _components(mask: np.ndarray, min_area: int = 10):
        """简单连通域标记，返回 [(x,y,w,h,area), ...]（小区域，BFS 足够快）。"""
        if mask.size == 0 or not mask.any():
            return []
        h, w = mask.shape
        visited = np.zeros_like(mask, dtype=bool)
        boxes = []
        for yy in range(h):
            for xx in range(w):
                if not mask[yy, xx] or visited[yy, xx]:
                    continue
                queue = deque([(yy, xx)])
                visited[yy, xx] = True
                minx = maxx = xx
                miny = maxy = yy
                area = 0
                while queue:
                    cy, cx = queue.popleft()
                    area += 1
                    if cx < minx: minx = cx
                    if cx > maxx: maxx = cx
                    if cy < miny: miny = cy
                    if cy > maxy: maxy = cy
                    for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            queue.append((ny, nx))
                if area >= min_area:
                    boxes.append((minx, miny, maxx - minx + 1, maxy - miny + 1, area))
        return boxes

    def _detect_video_rect(self, frame: Frame, content_right: int) -> Optional[Rect]:
        lum = luminance(frame.pixels)
        content = lum[:, :content_right]
        dark_rows = (content < 60).mean(axis=1) > 0.45
        start, end, count = longest_true_run(dark_rows, max_gap=3)
        if end - start < frame.height * 0.18:
            return None
        band = content[start:end + 1]
        dark_cols = (band < 60).mean(axis=0) > 0.45
        cstart, cend, ccount = longest_true_run(dark_cols, max_gap=8)
        if cend - cstart < content_right * 0.3:
            return None
        return Rect(cstart, start, cend, end)

    # ---------------------------------------------------------------- 按钮
    def _find_next_button(self, frame: Frame, doc: OcrDocument,
                          content_right: int, H: int) -> Optional[Button]:
        for ln in doc.lines:
            if ln.cx >= content_right:
                continue
            text = re.sub(r"[\s\-—_]", "", ln.text)
            if text in ("下一节", "下一节课程", "下一课"):
                return Button(text=ln.text, line=ln)
        # 像素兜底：底部蓝色圆角按钮
        rect = self._find_blue_button(frame, content_right, H)
        if rect is not None:
            fake = OcrLine("下一节", [], rect.x0, rect.y0, rect.x1, rect.y1)
            return Button(text="下一节", line=fake)
        return None

    def _find_blue_button(self, frame: Frame, content_right: int, H: int) -> Optional[Rect]:
        y0 = int(H * 0.86)
        region = frame.pixels[y0:H, :content_right]
        if region.size == 0:
            return None
        r = region[:, :, 0].astype(int)
        g = region[:, :, 1].astype(int)
        b = region[:, :, 2].astype(int)
        mask = (b > 190) & (b - r > 90) & (g > 90) & (g < 200) & (r < 130)
        if mask.sum() < 400:
            return None
        rows = mask.mean(axis=1)
        start, end, _ = longest_true_run(rows > 0.10, max_gap=2)
        if end <= start:
            return None
        band = mask[start:end + 1]
        cols = band.mean(axis=0)
        cstart, cend, _ = longest_true_run(cols > 0.20, max_gap=3)
        if cend - cstart < 40:
            return None
        return Rect(cstart, y0 + start, cend, y0 + end)

    # ------------------------------------------------------------- 弹窗/测验
    def _detect_popup(self, doc: OcrDocument, content_right: int, H: int,
                      tabs: Sequence[OcrLine]) -> Optional[PopupInfo]:
        lines = [ln for ln in doc.lines if ln.cx < content_right and ln.cy < H * 0.9]
        for ln in lines:
            text = ln.text.replace(" ", "")
            if len(text) <= 10 and text in POPUP_BUTTON_WORDS:
                return PopupInfo(kind="popup", text=text,
                                 button=Button(text, ln), lines=[ln])
        for ln in lines:
            text = ln.text.replace(" ", "")
            if len(text) <= 16 and any(w in text for w in POPUP_NOTICE_WORDS):
                return PopupInfo(kind="notice", text=text, lines=[ln])
        return None

    def _detect_quiz(self, doc: OcrDocument, content_right: int, H: int,
                     tabs: Sequence[OcrLine]) -> Optional[PopupInfo]:
        tab_y1 = max((ln.y1 for ln in tabs), default=0)
        for ln in doc.lines:
            if ln.cx >= content_right or ln.cy > H * 0.92:
                continue
            if ln.y0 < tab_y1 + 10 and "章节测验" in ln.text:
                continue  # 只是顶部标签页，不是正在答题
            if any(w in ln.text for w in QUIZ_WORDS):
                return PopupInfo(kind="quiz", text=ln.text, lines=[ln])
        return None
