# -*- coding: utf-8 -*-
"""拟人化鼠标：曲线移动、随机停顿、随机抖动，模拟真人观看时的操作习惯。"""
from __future__ import annotations

import math
import random
import time
from typing import Optional, Tuple

from . import win32


class HumanMouse:
    def __init__(self, config, logger=None):
        self.cfg = config
        self.logger = logger
        self._pos: Optional[Tuple[int, int]] = None

    # ------------------------------------------------------------------ 基础
    def position(self) -> Tuple[int, int]:
        if self._pos is None:
            self._pos = win32.get_cursor_pos()
        return self._pos

    def _set(self, x: int, y: int) -> None:
        win32.set_cursor_pos(x, y)
        self._pos = (int(x), int(y))

    def sleep(self, span: Tuple[float, float]) -> None:
        low, high = span
        time.sleep(random.uniform(low, high))

    # ------------------------------------------------------------ 曲线移动
    def move_to(self, x: int, y: int, steps: Optional[int] = None) -> None:
        sx, sy = self.position()
        tx, ty = int(x), int(y)
        dist = math.hypot(tx - sx, ty - sy)
        if dist < 2:
            self._set(tx, ty)
            return
        if steps is None:
            steps = max(6, min(24, int(dist / 22) + random.randint(2, 6)))
        # 控制点：让轨迹有一个自然的弧度
        mx = (sx + tx) / 2.0
        my = (sy + ty) / 2.0
        angle = math.atan2(ty - sy, tx - sx) + math.pi / 2
        offset = random.uniform(-0.12, 0.12) * dist
        cx = mx + math.cos(angle) * offset
        cy = my + math.sin(angle) * offset
        for i in range(1, steps + 1):
            t = i / float(steps)
            # ease-in-out
            e = 3 * t * t - 2 * t * t * t
            px = (1 - e) ** 2 * sx + 2 * (1 - e) * e * cx + e * e * tx
            py = (1 - e) ** 2 * sy + 2 * (1 - e) * e * cy + e * e * ty
            jitter = 0 if i == steps else random.uniform(-0.8, 0.8) * (1 - t)
            self._set(int(round(px + jitter)), int(round(py + jitter)))
            time.sleep(random.uniform(0.006, 0.016))
        if random.random() < 0.35:
            # 轻微过冲后回正，更像人手
            ox = tx + random.randint(-3, 3)
            oy = ty + random.randint(-3, 3)
            self._set(ox, oy)
            time.sleep(random.uniform(0.02, 0.06))
            self._set(tx, ty)

    def jitter(self, x: int, y: int, radius: int = 12) -> None:
        """在原位置附近轻微晃动（用于保持播放器控制条常显）。"""
        dx = random.randint(-radius, radius)
        dy = random.randint(-radius, radius)
        self.move_to(x + dx, y + dy, steps=random.randint(3, 7))

    # ------------------------------------------------------------------ 点击
    def click(self, x: int, y: int, double: bool = False) -> None:
        self.sleep(self.cfg.click_pause)
        self.move_to(x, y)
        time.sleep(random.uniform(0.04, 0.12))
        win32.mouse_down(x, y)
        time.sleep(random.uniform(0.05, 0.13))
        win32.mouse_up(x, y)
        if double:
            time.sleep(random.uniform(0.06, 0.12))
            win32.mouse_down(x, y)
            time.sleep(random.uniform(0.04, 0.09))
            win32.mouse_up(x, y)
        self.sleep(self.cfg.after_click_pause)

    def wheel(self, x: int, y: int, clicks: int) -> None:
        self.move_to(x, y)
        time.sleep(random.uniform(0.15, 0.3))
        for _ in range(abs(clicks)):
            win32.mouse_wheel(120 if clicks > 0 else -120)
            time.sleep(random.uniform(0.05, 0.15))
        time.sleep(random.uniform(0.4, 0.9))
