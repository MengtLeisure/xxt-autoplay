# -*- coding: utf-8 -*-
"""点击校验：每一次模拟点击之后，立刻做一次完整的图像识别。

这是本次迭代的核心改动之一：
    * 以前点击完直接进入下一步，页面没反应时只能靠“等超时”；
    * 现在点击 → 截图 → OCR → 解析 → 记录“点击后校验”结果，
      调用方可以立即知道页面到底变成了什么样，从而决定重试/继续。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from .analyzer import PageAnalyzer
from .config import Config
from .logger import Logger
from .model import PageState
from .mouse import HumanMouse
from .window import BrowserWindow


class ClickVerifier:
    """所有点击操作统一从这里走：点击后必须识别一次。"""

    def __init__(self, config: Config, logger: Logger, mouse: HumanMouse,
                 analyzer: PageAnalyzer):
        self.cfg = config
        self.logger = logger
        self.mouse = mouse
        self.analyzer = analyzer
        self.count = 0
        self.history: List[Dict] = []
        self.last_state: Optional[PageState] = None

    # ------------------------------------------------------------------ 点击
    def click(self, window: BrowserWindow, x: int, y: int,
              purpose: str = "") -> Optional[PageState]:
        """点击窗口坐标 (x, y)，随后立刻做一次完整图像识别并返回最新状态。"""
        self.count += 1
        name = purpose or f"点击#{self.count}"
        sx, sy = window.to_screen(int(x), int(y))
        self.logger.info(f"点击「{name}」，第 {self.count} 次点击")
        self.mouse.click(sx, sy)
        state = self.verify_after_click(name)
        return state

    def click_line(self, window: BrowserWindow, line, purpose: str = "") -> Optional[PageState]:
        return self.click(window, line.cx, line.cy, purpose=purpose)

    # ------------------------------------------------------------------ 校验
    def verify_after_click(self, purpose: str) -> Optional[PageState]:
        """点击后等待画面稳定，然后做一次完整识别（截图 + OCR + 解析）。"""
        time.sleep(self.cfg.verify_delay)
        frame, state = self.analyzer.scan(use_cache=False)
        if state is None:
            self.logger.warn(f"「{purpose}」点击后的图像识别失败（截图/OCR 异常）")
        else:
            self.last_state = state
            self.logger.info(f"点击后校验（识别完成）：{self.describe(state)}")
            self.history.append({"purpose": purpose, "state": state,
                                 "at": time.time()})
            if len(self.history) > 50:
                self.history.pop(0)
        return state

    # ------------------------------------------------------------------ 描述
    @staticmethod
    def describe(state: PageState) -> str:
        parts = ["学习通页面" if state.is_xxt_page else "非学习通页面"]
        if state.chapter_title:
            parts.append(f"章节={state.chapter_title}")
        if state.active_item is not None:
            parts.append(f"目录高亮={state.active_item.number}")
        parts.append(f"播放状态={state.video.play_state}")
        if state.video.time_total > 0:
            parts.append(f"时间={state.video.time_text}")
        if state.video.center_play:
            parts.append("画面中央=大播放按钮")
        if state.popup is not None:
            parts.append(f"弹窗={state.popup.text}")
        if state.quiz is not None:
            parts.append(f"测验={state.quiz.text}")
        return " | ".join(parts)
