# -*- coding: utf-8 -*-
"""屏幕捕获：把浏览器窗口内容抓成 RGB 数组，并提供像素级小工具。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageGrab

from . import win32


@dataclass
class Frame:
    image: Image.Image
    pixels: np.ndarray       # (H, W, 3) int16
    origin: Tuple[int, int]  # 图像左上角对应的屏幕坐标
    scale: float = 1.0

    @property
    def width(self) -> int:
        return self.pixels.shape[1]

    @property
    def height(self) -> int:
        return self.pixels.shape[0]

    def crop(self, left: int, top: int, right: int, bottom: int) -> "Frame":
        left = max(0, min(self.width, left))
        top = max(0, min(self.height, top))
        right = max(left + 1, min(self.width, right))
        bottom = max(top + 1, min(self.height, bottom))
        img = self.image.crop((left, top, right, bottom))
        return Frame(img, np.asarray(img).astype(np.int16),
                     (self.origin[0] + left, self.origin[1] + top), self.scale)

    def screen_point(self, x: int, y: int) -> Tuple[int, int]:
        return self.origin[0] + int(x), self.origin[1] + int(y)


class ScreenCapture:
    """负责抓取指定窗口区域的画面（窗口坐标 = 图像坐标）。"""

    def __init__(self, window_rect: win32.RECT):
        self.rect = window_rect
        # WindowInfo 的矩形已经是扩展边框（不含阴影），直接定位
        self._bbox = (int(window_rect.left), int(window_rect.top),
                      int(window_rect.right), int(window_rect.bottom))

    def update_rect(self, rect: win32.RECT) -> None:
        self.rect = rect
        self._bbox = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))

    def grab(self) -> Optional[Frame]:
        try:
            img = ImageGrab.grab(bbox=self._bbox, all_screens=True)
            if img is None:
                return None
            img = img.convert("RGB")
            arr = np.asarray(img).astype(np.int16)
            return Frame(img, arr, (self._bbox[0], self._bbox[1]))
        except Exception:
            return None

    @staticmethod
    def grab_image(image_path: str) -> Frame:
        img = Image.open(image_path).convert("RGB")
        return Frame(img, np.asarray(img).astype(np.int16), (0, 0))


def luminance(pixels: np.ndarray) -> np.ndarray:
    """亮度（0-255）。pixels: (H, W, 3)"""
    return (0.299 * pixels[:, :, 0] + 0.587 * pixels[:, :, 1] + 0.114 * pixels[:, :, 2])


def sample_color(pixels: np.ndarray, x: int, y: int) -> Tuple[int, int, int]:
    h, w = pixels.shape[:2]
    x = max(0, min(w - 1, int(x)))
    y = max(0, min(h - 1, int(y)))
    px = pixels[y, x]
    return int(px[0]), int(px[1]), int(px[2])


def color_close(color, target, tolerance: int) -> bool:
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(color, target))


def longest_true_run(mask: np.ndarray, max_gap: int = 6) -> Tuple[int, int, int]:
    """在一维布尔数组中找最长的一段“基本连续为真”的区间（允许小缺口）。"""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return 0, -1, 0
    best_start = start = int(idx[0])
    best_end = end = int(idx[0])
    count = 1
    best_count = 1
    for i in range(1, idx.size):
        cur = int(idx[i])
        if cur - end <= max_gap + 1:
            count += 1
        else:
            start = cur
            count = 1
        end = cur
        if count > best_count:
            best_count = count
            best_start, best_end = start, end
    return best_start, best_end, best_count


def frame_motion(pixels_a: np.ndarray, pixels_b: np.ndarray, rect=None,
                 diff_threshold: int = 18, exclude_bottom: int = 0) -> float:
    """比较两张截图的画面变化比例（0~1），用来判断视频是否“真的在动”。

    rect 可以限定只比较视频画面区域（Rect 对象，需有 x0/y0/x1/y1），
    exclude_bottom 用于排除底部控制条（按钮闪烁会干扰判断）。
    """
    if pixels_a is None or pixels_b is None:
        return 0.0
    h = min(pixels_a.shape[0], pixels_b.shape[0])
    w = min(pixels_a.shape[1], pixels_b.shape[1])
    if rect is not None:
        x0 = max(0, int(rect.x0))
        y0 = max(0, int(rect.y0))
        x1 = min(w, int(rect.x1))
        y1 = min(h, int(rect.y1) - int(exclude_bottom))
        if x1 - x0 < 20 or y1 - y0 < 20:
            x0, y0, x1, y1 = 0, 0, w, h
    else:
        x0, y0, x1, y1 = 0, 0, w, h
    a = pixels_a[y0:y1, x0:x1].astype(np.int32)
    b = pixels_b[y0:y1, x0:x1].astype(np.int32)
    if a.size == 0 or b.shape != a.shape:
        return 0.0
    diff = np.abs(a - b).max(axis=2)
    return float((diff > diff_threshold).mean())

