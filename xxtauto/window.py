# -*- coding: utf-8 -*-
"""浏览器窗口对象：定位、激活、截图。"""
from __future__ import annotations

import time
from typing import Optional

from . import win32
from .screen import Frame, ScreenCapture


class BrowserWindow:
    """代表学习通所在的浏览器窗口。"""

    def __init__(self, info: win32.WindowInfo, logger=None):
        self.hwnd = info.hwnd
        self.title = info.title
        self.rect = info.rect
        self.logger = logger
        self._capture = ScreenCapture(self.rect)

    # ------------------------------------------------------------------ 定位
    @classmethod
    def locate(cls, config, logger=None) -> Optional["BrowserWindow"]:
        info = win32.find_window(config.window_title_keywords,
                                 config.browser_hint_keywords)
        if info is None:
            return None
        if logger:
            logger.info(f"找到浏览器窗口：{info.title}  ({info.rect.width}x{info.rect.height})")
        return cls(info, logger)

    def is_alive(self) -> bool:
        try:
            return bool(win32.user32.IsWindow(self.hwnd))
        except Exception:
            return False

    # ------------------------------------------------------------------ 激活
    def activate(self, maximize: bool = True) -> bool:
        ok = win32.activate_window(self.hwnd, maximize=maximize)
        time.sleep(0.4)
        self.refresh_rect()
        return ok

    def ensure_foreground(self) -> bool:
        if win32.is_foreground(self.hwnd):
            return True
        ok = win32.activate_window(self.hwnd, maximize=True)
        time.sleep(0.3)
        self.refresh_rect()
        return ok

    def refresh_rect(self) -> None:
        rect = win32._window_rect(self.hwnd)
        if rect.width > 100 and rect.height > 100:
            self.rect = rect
            self._capture.update_rect(rect)

    # ------------------------------------------------------------------ 截图
    def capture(self) -> Optional[Frame]:
        return self._capture.grab()

    def to_screen(self, x: int, y: int):
        return int(self.rect.left) + int(x), int(self.rect.top) + int(y)
