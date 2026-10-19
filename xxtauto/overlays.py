# -*- coding: utf-8 -*-
"""弹窗处理与“章节测验”等人机协作流程。

第二轮迭代：关闭弹窗的点击也统一走 ClickVerifier —— 点完立即识别，
确认弹窗真的消失了；如果没消失，下一轮会再次尝试。
"""
from __future__ import annotations

import time
from typing import Optional

from . import win32
from .analyzer import PageAnalyzer
from .config import Config
from .errors import StopRequested
from .interaction import ClickVerifier
from .logger import Logger
from .model import PageState
from .mouse import HumanMouse


class OverlayHandler:
    """负责关闭各种正常/异常弹窗，以及章节测验时暂停等待人工完成。"""

    def __init__(self, config: Config, logger: Logger, mouse: HumanMouse,
                 analyzer: PageAnalyzer, verifier: ClickVerifier):
        self.cfg = config
        self.logger = logger
        self.mouse = mouse
        self.analyzer = analyzer
        self.verifier = verifier
        self._handled_texts = set()

    # ------------------------------------------------------------ 弹窗处理
    def handle_state_overlays(self, state: PageState, window) -> str:
        """返回 'popup'（已处理弹窗）、'quiz'（遇到测验）或 'none'。"""
        if state is None:
            return "none"
        if state.quiz is not None:
            self.logger.warn(f"检测到章节测验：{state.quiz.text}")
            return "quiz"
        if state.popup is not None:
            self._dismiss(state, window)
            return "popup"
        return "none"

    def _dismiss(self, state: PageState, window) -> bool:
        popup = state.popup
        if popup is None:
            return False
        key = (popup.kind, popup.text)
        if key in self._handled_texts and popup.button is None:
            return False
        if popup.button is not None:
            new_state = self.verifier.click_line(
                window, popup.button.line,
                purpose=f"关闭弹窗按钮「{popup.text}」")
            if new_state is not None and new_state.popup is None:
                self.logger.ok("点击后校验：弹窗已关闭")
        else:
            self.logger.info(f"识别到提示：{popup.text}（尝试点击安全位置关闭）")
            cx = max(50, state.sidebar_left // 2)
            cy = int(window.rect.height * 0.72)
            self.verifier.click(window, cx, cy, purpose=f"关闭提示「{popup.text}」")
        self._handled_texts.add(key)
        time.sleep(0.6)
        return True

    # ------------------------------------------------------------ 章节测验
    def wait_manual_quiz(self, state: Optional[PageState]) -> None:
        """暂停自动流程，等待用户手动完成章节测验后继续。"""
        if not self.cfg.prompt_quiz:
            return
        self.logger.section("需要人工完成章节测验")
        self.logger.warn("请在浏览器中手动完成章节测验；完成后程序会自动继续。")
        self.logger.warn("（也可以直接按 Esc 停止整个任务）")
        deadline = time.time() + 7200
        entered_grace = 0
        while time.time() < deadline:
            if win32.escape_pressed():
                raise StopRequested()
            if win32.enter_pressed():
                entered_grace += 1
                if entered_grace > 3:
                    self.logger.info("检测到回车，继续自动播放")
                    return
            else:
                entered_grace = 0
            window = self.analyzer.window
            window.ensure_foreground()
            _, new_state = self.analyzer.scan(use_cache=False)
            if new_state is not None and new_state.quiz is None:
                time.sleep(1.5)
                _, confirm = self.analyzer.scan(use_cache=False)
                if confirm is not None and confirm.quiz is None:
                    self.logger.ok("章节测验已完成，继续自动播放")
                    return
            time.sleep(3.0)
        self.logger.warn("等待章节测验超时，继续尝试自动播放")
