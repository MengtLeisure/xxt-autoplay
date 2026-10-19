# -*- coding: utf-8 -*-
"""页面分析服务：整合截图与 OCR，提供全量识别和快速（纯像素）识别。"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from .config import Config
from .logger import Logger
from .model import PageState
from .ocr import OcrDocument, OcrEngine
from .parser import XxtPageParser
from .screen import Frame
from .window import BrowserWindow


class PageAnalyzer:
    def __init__(self, config: Config, logger: Logger, window: BrowserWindow):
        self.cfg = config
        self.logger = logger
        self.window = window
        self.ocr = OcrEngine(logger=logger)
        self.parser = XxtPageParser(config, logger)
        self.last_state: Optional[PageState] = None
        self.last_ocr_time: float = 0.0

    # ------------------------------------------------------------ 全量识别
    def scan(self, use_cache: bool = True) -> Tuple[Optional[Frame], Optional[PageState]]:
        frame = self.window.capture()
        if frame is None:
            return None, None
        doc = self.ocr.recognize(frame, use_cache=use_cache)
        if doc is None:
            return frame, None
        state = self.parser.parse(frame, doc, previous=self.last_state)
        self.last_state = state
        self.last_ocr_time = time.time()
        return frame, state

    # ------------------------------------------------------------ 快速识别
    def scan_fast(self, state: PageState) -> Optional[Frame]:
        frame = self.window.capture()
        if frame is None or state is None:
            return frame
        try:
            self.parser.refresh_video(frame, state)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(f"快速识别异常：{exc}")
        return frame

    # ------------------------------------------------------------ 等待页面
    def wait_for_page(self, timeout: float = 180.0) -> Optional[PageState]:
        deadline = time.time() + timeout
        last_log = 0.0
        while time.time() < deadline:
            _, state = self.scan()
            if state is not None and state.is_xxt_page and state.layout_ready:
                self.logger.ok(f"页面识别成功：{state.chapter_title or '学习通章节页'}")
                return state
            now = time.time()
            if now - last_log > 8:
                last_log = now
                self.logger.warn("尚未识别到学习通课程页面，请确认 Edge 已打开“学生学习页面”…")
            time.sleep(1.5)
        return None
