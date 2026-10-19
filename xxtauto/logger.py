# -*- coding: utf-8 -*-
"""日志系统：控制台彩色输出 + 文件记录 + 出错截图。"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

from .config import data_dir


class LogLevel:
    INFO = "INFO"
    OK = "OK"
    WARN = "WARN"
    ERROR = "ERROR"
    DEBUG = "DEBUG"


class Logger:
    """同时写控制台和日志文件，支持中文。"""

    COLORS = {
        LogLevel.INFO: "\x1b[37m",
        LogLevel.OK: "\x1b[92m",
        LogLevel.WARN: "\x1b[93m",
        LogLevel.ERROR: "\x1b[91m",
        LogLevel.DEBUG: "\x1b[90m",
    }
    RESET = "\x1b[0m"

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = os.path.join(data_dir(), "logs", stamp)
        os.makedirs(self.run_dir, exist_ok=True)
        self.log_path = os.path.join(self.run_dir, "run.log")
        self._fh = open(self.log_path, "a", encoding="utf-8")
        self._use_color = self._console_supports_color()

    @staticmethod
    def _console_supports_color() -> bool:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            h = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)) == 0:
                return False
            return kernel32.SetConsoleMode(h, mode.value | 0x0004) != 0
        except Exception:
            return False

    def _write(self, level: str, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        try:
            if self._use_color:
                color = self.COLORS.get(level, "")
                print(color + line + self.RESET, flush=True)
            else:
                print(line, flush=True)
        except UnicodeEncodeError:
            # 控制台编码不支持某些字符（例如窗口标题里的零宽空格）时降级输出
            safe = line.encode("gbk", "replace").decode("gbk", "replace")
            print(safe, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def info(self, msg: str) -> None:
        self._write(LogLevel.INFO, msg)

    def ok(self, msg: str) -> None:
        self._write(LogLevel.OK, msg)

    def warn(self, msg: str) -> None:
        self._write(LogLevel.WARN, msg)

    def error(self, msg: str) -> None:
        self._write(LogLevel.ERROR, msg)

    def debug(self, msg: str) -> None:
        if self.verbose:
            self._write(LogLevel.DEBUG, msg)

    def section(self, title: str) -> None:
        self._write(LogLevel.INFO, "─" * 12 + f" {title} " + "─" * 12)

    def save_shot(self, image, tag: str) -> str:
        """保存一张调试/出错截图，返回路径。"""
        try:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:40]
            path = os.path.join(self.run_dir, f"{int(time.time())}_{safe}.png")
            image.save(path)
            return path
        except Exception:
            return ""

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
