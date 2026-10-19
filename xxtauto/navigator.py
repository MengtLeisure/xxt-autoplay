# -*- coding: utf-8 -*-
"""课程导航：点击“下一节”/下一行目录，目录滚动，并确认页面真的切换了。

第二轮迭代：所有点击都走 ClickVerifier —— 点完立刻做一次图像识别校验，
立即判断“是否已经切换”，不再只依赖超时等待。
"""
from __future__ import annotations

import time
from typing import Optional, Sequence, Set, Tuple

from . import win32
from .analyzer import PageAnalyzer
from .config import Config
from .errors import StopRequested
from .interaction import ClickVerifier
from .logger import Logger
from .model import PageState, SidebarItem
from .mouse import HumanMouse


class ChapterNavigator:
    """负责从当前章节切换到下一章节，包括目录滚动。"""

    def __init__(self, config: Config, logger: Logger, mouse: HumanMouse,
                 analyzer: PageAnalyzer, verifier: ClickVerifier):
        self.cfg = config
        self.logger = logger
        self.mouse = mouse
        self.analyzer = analyzer
        self.verifier = verifier

    # ------------------------------------------------------------------ 主流程
    def go_next(self, state: PageState, current_number: str,
                visited: Set[str]) -> Tuple[Optional[PageState], str]:
        """切换到下一节。

        返回 (新状态, 方式)，方式取值：button / sidebar / end / failed。
        """
        window = self.analyzer.window
        old_title = state.chapter_title
        scan_state = state
        button_tried = False
        last_numbers = [it.number for it in state.items]

        # 0) 先试底部「下一节」按钮：走页面自身顺序，不会漏掉章节
        if scan_state.next_button is not None:
            button_tried = True
            new_state = self.verifier.click_line(
                window, scan_state.next_button.line, purpose="底部「下一节」按钮")
            if self._switched(old_title, scan_state, new_state):
                self._after_switch(new_state)
                return new_state, "button"
            self.logger.info("「下一节」按钮暂未生效，改为点击目录中的下一节")

        for attempt in range(self.cfg.max_scroll_attempts):
            # 1) 目录里可见的下一行
            item = self._find_after(scan_state, current_number, visited)
            if item is not None:
                new_state = self.verifier.click_line(
                    window, item.line, purpose=f"目录中的下一节 {item.number} {item.title}")
                if self._switched(old_title, scan_state, new_state):
                    self._after_switch(new_state)
                    return new_state, "sidebar"
                self.logger.warn("点击目录后页面没有变化，继续尝试")

            # 2) 再次尝试底部“下一节”按钮（只尝试一次）
            if not button_tried and scan_state.next_button is not None:
                button_tried = True
                new_state = self.verifier.click_line(
                    window, scan_state.next_button.line, purpose="底部「下一节」按钮")
                if self._switched(old_title, scan_state, new_state):
                    self._after_switch(new_state)
                    return new_state, "button"

            # 3) 目录不够长时向下滚动，看看后面还有没有章节
            if attempt >= self.cfg.max_scroll_attempts - 1:
                break
            new_scan = self.scroll_sidebar(scan_state)
            if new_scan is None:
                break
            scan_state = new_scan
            numbers = [it.number for it in scan_state.items]
            if numbers == last_numbers:
                self.logger.info("目录已滚到底，没有更多章节")
                break
            last_numbers = numbers

        # 4) 判断是否已经到课程末尾
        if self._find_after(scan_state, current_number, visited) is None:
            return None, "end"
        return None, "failed"

    def _after_switch(self, state: PageState) -> None:
        if state is None:
            return
        self.logger.ok(f"点击后校验：已切换到「{state.chapter_title or '新章节'}」")
        time.sleep(self.cfg.start_offset_seconds)

    @staticmethod
    def _switched(old_title: str, old_state: PageState,
                  new_state: Optional[PageState]) -> bool:
        if new_state is None:
            return False
        if new_state.quiz is not None:
            return True
        if new_state.chapter_title and new_state.chapter_title != old_title:
            return True
        old_number = old_state.active_item.number if old_state.active_item else ""
        new_number = new_state.active_item.number if new_state.active_item else ""
        if new_number and new_number != old_number:
            return True
        return False

    # --------------------------------------------------------------- 目录滚动
    def scroll_sidebar(self, state: PageState) -> Optional[PageState]:
        window = self.analyzer.window
        left = state.sidebar_left
        right = state.sidebar_right or window.rect.width
        cx = left + (right - left) // 2
        cy = int(window.rect.height * 0.55)
        self.logger.info("目录已到可见底部，向下滚动继续查找后续章节")
        self.mouse.wheel(*window.to_screen(cx, cy), -4)
        time.sleep(0.8)
        _, new_state = self.analyzer.scan(use_cache=False)
        if new_state is None:
            return None
        if self._same_items(state, new_state):
            self.logger.info("目录滚动后没有出现新章节，可能已经到底")
            return new_state
        return new_state

    @staticmethod
    def _same_items(a: PageState, b: PageState) -> bool:
        na = [it.number for it in a.items]
        nb = [it.number for it in b.items]
        return na == nb

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _find_after(state: PageState, current_number: str,
                    visited: Set[str]) -> Optional[SidebarItem]:
        """找当前章节之后的一行：优先按屏幕上的上下顺序，其次按章节号。"""
        if not state.items:
            return None
        cur_item = state.item_by_number(current_number)
        if cur_item is None and state.active_item is not None:
            cur_item = state.active_item
        candidates = []
        if cur_item is not None and cur_item in state.items:
            idx = state.items.index(cur_item)
            candidates = [it for it in state.items[idx + 1:] if it.number not in visited]
        else:
            try:
                cur = int(current_number.split(".")[0]), int(current_number.split(".")[1])
            except Exception:
                cur = None
            if cur is not None:
                candidates = [it for it in state.items
                              if it.number_tuple() > cur and it.number not in visited]
        if candidates:
            return candidates[0]
        return None

    # ------------------------------------------------------------ 等待切换
    def _wait_switch(self, old_title: str, old_state: PageState,
                     timeout: Optional[float] = None) -> Optional[PageState]:
        window = self.analyzer.window
        deadline = time.time() + (timeout or self.cfg.chapter_switch_timeout)
        old_number = old_state.active_item.number if old_state.active_item else ""
        while time.time() < deadline:
            if win32.escape_pressed():
                raise StopRequested()
            time.sleep(1.2)
            window.ensure_foreground()
            frame, state = self.analyzer.scan(use_cache=False)
            if state is None:
                continue
            if state.quiz is not None:
                self.logger.warn("切换后进入章节测验页面")
                return state
            if state.popup is not None and state.popup.button is not None:
                new_state = self.verifier.click_line(
                    window, state.popup.button.line,
                    purpose=f"关闭弹窗：{state.popup.text}")
                if new_state is not None:
                    state = new_state
                continue
            new_number = state.active_item.number if state.active_item else ""
            if state.chapter_title and state.chapter_title != old_title:
                self.logger.ok(f"已切换到：{state.chapter_title}")
                time.sleep(self.cfg.start_offset_seconds)
                return state
            if new_number and new_number != old_number:
                self.logger.ok(f"目录高亮已切换：{new_number}")
                time.sleep(self.cfg.start_offset_seconds)
                return state
        return None
