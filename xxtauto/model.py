# -*- coding: utf-8 -*-
"""页面数据模型：把识别结果组织成面向对象的结构，供控制器/播放器/导航器使用。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .ocr import OcrDocument, OcrLine


class PlayState:
    PLAYING = "playing"      # 播放中（控制条显示暂停键）
    PAUSED = "paused"        # 已暂停（显示播放键）
    UNKNOWN = "unknown"
    NO_VIDEO = "no_video"


@dataclass
class Rect:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)

    @property
    def cx(self) -> int:
        return (self.x0 + self.x1) // 2

    @property
    def cy(self) -> int:
        return (self.y0 + self.y1) // 2

    def contains(self, x: int, y: int) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


@dataclass
class SidebarItem:
    """目录里的一个章节条目（如 1.16 牢记中国国防耻辱（下））。"""
    number: str
    title: str
    line: OcrLine
    active: bool = False
    section: str = ""

    @property
    def display(self) -> str:
        return f"{self.number} {self.title}".strip()

    @property
    def center(self) -> Tuple[int, int]:
        return self.line.cx, self.line.cy

    def number_tuple(self):
        try:
            a, b = self.number.split(".")
            return int(a), int(b)
        except Exception:
            return (0, 0)


@dataclass
class VideoPanel:
    """播放器状态：进度条、时间、播放/暂停、悬停点。"""
    bar_rect: Optional[Rect] = None
    bar_fill: Optional[float] = None          # 进度条已播放比例 0~1
    time_current: int = 0                     # 秒
    time_total: int = 0                       # 秒
    play_state: str = PlayState.UNKNOWN
    hover_point: Optional[Tuple[int, int]] = None
    icon_center: Optional[Tuple[int, int]] = None
    video_rect: Optional[Rect] = None
    bar_row_y: int = 0          # 进度条所在行（快速刷新的搜索中心）
    bar_search_y: int = 0       # 时间文字所在行
    center_play: bool = False   # 视频中央是否显示“大播放按钮”（说明还没开始播放）

    @property
    def ratio(self) -> Optional[float]:
        if self.time_total > 0:
            return min(1.0, self.time_current / self.time_total)
        return self.bar_fill

    @property
    def time_text(self) -> str:
        if self.time_total <= 0:
            return "--:-- / --:--"
        return f"{fmt_time(self.time_current)} / {fmt_time(self.time_total)}"


def fmt_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@dataclass
class Button:
    text: str
    line: OcrLine

    @property
    def center(self) -> Tuple[int, int]:
        return self.line.cx, self.line.cy


@dataclass
class PopupInfo:
    """识别到的弹窗/提示。"""
    kind: str                    # popup / notice / quiz
    text: str
    button: Optional[Button] = None
    lines: List[OcrLine] = field(default_factory=list)


@dataclass
class PageState:
    is_xxt_page: bool = False
    layout_ready: bool = False
    chapter_title: str = ""
    sidebar_left: int = 0
    sidebar_right: int = 0
    items: List[SidebarItem] = field(default_factory=list)
    active_item: Optional[SidebarItem] = None
    video: VideoPanel = field(default_factory=VideoPanel)
    next_button: Optional[Button] = None
    popup: Optional[PopupInfo] = None
    quiz: Optional[PopupInfo] = None
    doc: Optional[OcrDocument] = None
    timestamp: float = 0.0

    # ---- 便捷方法 ----
    def item_by_number(self, number: str) -> Optional[SidebarItem]:
        for it in self.items:
            if it.number == number:
                return it
        return None

    def next_item(self) -> Optional[SidebarItem]:
        if not self.active_item:
            return None
        idx = self.items.index(self.active_item)
        if idx + 1 < len(self.items):
            return self.items[idx + 1]
        return None

    def item_after_number(self, number: str) -> Optional[SidebarItem]:
        cur = self.item_by_number(number)
        if cur is None:
            return None
        idx = self.items.index(cur)
        if idx + 1 < len(self.items):
            return self.items[idx + 1]
        return None

    def sidebar_span(self) -> Tuple[int, int]:
        return self.sidebar_left, self.sidebar_right
