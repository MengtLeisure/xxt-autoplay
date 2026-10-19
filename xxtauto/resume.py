# -*- coding: utf-8 -*-
"""断点续播：把“正在播放/已完成”的章节记录下来，下次启动自动接上。

优化迭代之一：长时间播放课程时，程序可能被 Esc 停止、断电、重启，
再次运行时不需要手动翻目录找位置。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from .config import data_dir


class ResumeStore:
    """保存/读取续播状态（JSON 文件）。"""

    def __init__(self, logger=None):
        self.logger = logger
        self.path = os.path.join(data_dir(), "resume.json")

    # ------------------------------------------------------------------ 读
    def load(self) -> Optional[Dict]:
        try:
            if not os.path.isfile(self.path):
                return None
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    # ------------------------------------------------------------------ 写
    def save(self, chapter_number: str, chapter_title: str,
             completed: List[str], extra: Optional[Dict] = None) -> None:
        try:
            payload = {
                "chapter_number": chapter_number,
                "chapter_title": chapter_title,
                "completed": list(completed),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if extra:
                payload.update(extra)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001
            if self.logger:
                self.logger.debug(f"保存续播状态失败：{exc}")

    # ------------------------------------------------------------------ 清
    def clear(self) -> None:
        try:
            if os.path.isfile(self.path):
                os.remove(self.path)
        except Exception:
            pass
