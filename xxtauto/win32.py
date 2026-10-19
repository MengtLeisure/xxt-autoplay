# -*- coding: utf-8 -*-
"""Win32 底层封装：DPI、窗口查找/激活、鼠标模拟、全局按键检测。

本程序只使用“屏幕图像 + 模拟真人鼠标操作”，不接触网页 DOM/脚本接口。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from dataclasses import dataclass
from typing import List, Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

# ---------------- 常量 ----------------
SW_RESTORE = 9
SW_MAXIMIZE = 3
SW_SHOW = 5
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_MOVE = 0x0001
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SM_CXSCREEN = 0
SM_CYSCREEN = 1


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_tuple(self):
        return (self.left, self.top, self.right, self.bottom)

    def __repr__(self) -> str:
        return f"RECT({self.left},{self.top},{self.right},{self.bottom})"


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    rect: RECT

    @property
    def area(self) -> int:
        return max(0, self.rect.width) * max(0, self.rect.height)


# ---------------- DPI ----------------
def enable_dpi_awareness() -> int:
    """开启 Per-Monitor DPI 感知，保证截图/坐标与物理像素一致。"""
    try:
        shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return 2
    except Exception:
        try:
            user32.SetProcessDPIAware()
            return 1
        except Exception:
            return 0


def get_dpi_for_window(hwnd: int) -> int:
    try:
        return int(user32.GetDpiForWindow(hwnd))
    except Exception:
        return 96


def virtual_screen_size():
    return (user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN))


# ---------------- 窗口枚举 ----------------
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        n = 512
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _window_rect(hwnd: int) -> RECT:
    rect = RECT()
    try:
        if dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                                        ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
            if rect.width > 50 and rect.height > 50:
                return rect
    except Exception:
        pass
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect))
    return rect


def list_windows(visible_only: bool = True) -> List[WindowInfo]:
    result: List[WindowInfo] = []

    def _cb(hwnd, lparam):
        try:
            if visible_only and not user32.IsWindowVisible(hwnd):
                return True
            title = _window_title(hwnd)
            if not title.strip():
                return True
            rect = _window_rect(hwnd)
            if rect.width < 100 or rect.height < 100:
                return True
            result.append(WindowInfo(int(hwnd), title, rect))
        except Exception:
            pass
        return True

    enum_proc = WNDENUMPROC(_cb)
    user32.EnumWindows(enum_proc, 0)
    return result


def find_window(keywords, fallback_keywords=()) -> Optional[WindowInfo]:
    """按标题关键字找窗口；先精确匹配 keywords，再尝试 fallback_keywords。"""
    windows = list_windows()
    key_list = [k for k in keywords if k]
    matched = [w for w in windows if any(k in w.title for k in key_list)]
    if not matched:
        fb = [k for k in fallback_keywords if k]
        matched = [w for w in windows if any(k in w.title for k in fb)]
    if not matched:
        return None
    matched.sort(key=lambda w: w.area, reverse=True)
    return matched[0]


def _get_foreground() -> int:
    return int(user32.GetForegroundWindow())


def activate_window(hwnd: int, maximize: bool = True) -> bool:
    """把窗口带到前台；若系统拒绝，用 AttachThreadInput 兜底。"""
    try:
        user32.ShowWindow(wt.HWND(hwnd), SW_RESTORE if not maximize else SW_MAXIMIZE)
        time.sleep(0.15)
        if maximize:
            user32.ShowWindow(wt.HWND(hwnd), SW_MAXIMIZE)
        for _ in range(3):
            if user32.SetForegroundWindow(wt.HWND(hwnd)):
                pass
            fg = _get_foreground()
            if fg == hwnd:
                return True
            # 模拟一次 ALT 键，破解前台锁定
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            tid_fg = user32.GetWindowThreadProcessId(wt.HWND(fg), None)
            tid_me = kernel32.GetCurrentThreadId()
            if tid_fg and tid_fg != tid_me:
                attached = user32.AttachThreadInput(tid_me, tid_fg, True)
                user32.SetForegroundWindow(wt.HWND(hwnd))
                user32.BringWindowToTop(wt.HWND(hwnd))
                if attached:
                    user32.AttachThreadInput(tid_me, tid_fg, False)
            time.sleep(0.2)
            if _get_foreground() == hwnd:
                return True
        return _get_foreground() == hwnd
    except Exception:
        return False


def is_foreground(hwnd: int) -> bool:
    return _get_foreground() == hwnd


def ensure_foreground(hwnd: int) -> bool:
    if is_foreground(hwnd):
        return True
    return activate_window(hwnd, maximize=True)


# ---------------- 窗口相对坐标 ----------------
class Coordinate:
    """屏幕坐标 <-> 窗口坐标 转换。"""

    def __init__(self, rect: RECT):
        self.rect = rect

    def to_screen(self, x: int, y: int):
        return self.rect.left + int(x), self.rect.top + int(y)

    def to_window(self, sx: int, sy: int):
        return int(sx) - self.rect.left, int(sy) - self.rect.top


# ---------------- 鼠标 ----------------
def get_cursor_pos():
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def set_cursor_pos(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def _mouse_event(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
    user32.mouse_event(flags, ctypes.c_long(dx), ctypes.c_long(dy),
                       ctypes.c_long(data), ctypes.c_ulong(0))


def mouse_down(x: int, y: int) -> None:
    set_cursor_pos(x, y)
    time.sleep(0.03)
    _mouse_event(MOUSEEVENTF_LEFTDOWN)


def mouse_up(x: int, y: int) -> None:
    _mouse_event(MOUSEEVENTF_LEFTUP)


def mouse_wheel(delta: int) -> None:
    _mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(delta))


# ---------------- 键盘状态 ----------------
def key_pressed(vk: int) -> bool:
    try:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def escape_pressed() -> bool:
    return key_pressed(VK_ESCAPE)


def enter_pressed() -> bool:
    return key_pressed(VK_RETURN)


# ---------------- 控制台字体 ----------------
class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class CONSOLE_FONT_INFOEX(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("nFont", ctypes.c_ulong),
                ("dwFontSize", COORD),
                ("FontFamily", ctypes.c_uint),
                ("FontWeight", ctypes.c_uint),
                ("FaceName", ctypes.c_wchar * 32)]


def set_console_font(face: str = "Microsoft YaHei", size: int = 18) -> None:
    try:
        handle = kernel32.GetStdHandle(-11)
        info = CONSOLE_FONT_INFOEX()
        info.cbSize = ctypes.sizeof(CONSOLE_FONT_INFOEX)
        if not kernel32.GetCurrentConsoleFontEx(handle, False, ctypes.byref(info)):
            return
        info.dwFontSize = COORD(0, size)
        info.FontFamily = 54  # FF_MODERN | TMPF_VECTOR ... 用现代字体族
        info.FontWeight = 400
        info.FaceName = face
        kernel32.SetCurrentConsoleFontEx(handle, False, ctypes.byref(info))
    except Exception:
        pass


def set_console_title(title: str) -> None:
    try:
        kernel32.SetConsoleTitleW(ctypes.c_wchar_p(title))
    except Exception:
        pass
