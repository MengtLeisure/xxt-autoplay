# -*- coding: utf-8 -*-
"""全局配置：集中管理识别阈值、时间节奏等参数。

所有参数都可以通过 %LOCALAPPDATA%\\XxtAutoPlay\\config.json 覆盖，
程序第一次运行会自动生成一份默认配置文件，方便后续微调。
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, asdict, field


def data_dir() -> str:
    """日志/配置目录：优先 %LOCALAPPDATA%\\XxtAutoPlay，不可写时退回临时目录。"""
    candidates = []
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    candidates.append(os.path.join(base, "XxtAutoPlay"))
    candidates.append(os.path.join(tempfile.gettempdir(), "XxtAutoPlay"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_data"))
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return d
        except Exception:
            continue
    return tempfile.gettempdir()


@dataclass
class Config:
    # ---------- 浏览器窗口 ----------
    window_title_keywords: tuple = ("学生学习页面", "学习通")
    browser_hint_keywords: tuple = ("Microsoft Edge",)

    # ---------- 页面识别 ----------
    sidebar_tab_words: tuple = ("目录", "讨论", "笔记", "标注")
    page_marker_words: tuple = ("章节详情", "返回课程", "目录", "任务点")
    # 侧边栏章节标题形如 "1.16 牢记中国国防耻辱（下）"
    chapter_number_re: str = r"^(\d+)[\.．。](\d+)(.*)$"
    section_number_re: str = r"^(\d+)[^\d\.．。:：].*$"
    # 激活行背景色（浅蓝）容差
    active_bg_color: tuple = (240, 246, 255)
    active_bg_tolerance: int = 10

    # ---------- 播放控制 ----------
    completion_ratio: float = 0.95      # 观看进度达到 95% 即视为看完
    start_offset_seconds: int = 3       # 进入新章节后等待播放器加载的时间
    poll_interval: float = 1.0          # 快速轮询间隔（秒）
    ocr_interval: float = 7.0           # OCR 识别间隔（秒），太快会占用较多 CPU
    hover_jitter: int = 14              # 悬停点随机抖动像素，模拟真人手抖
    progress_stall_warn: float = 90.0   # 进度卡住的告警阈值（秒）
    progress_stall_recover: float = 150.0  # 进度卡住后主动恢复的阈值（秒）
    chapter_switch_timeout: float = 15.0   # 点击下一节后等待页面切换的超时
    max_scroll_attempts: int = 6        # 侧边栏向下滚动查找后续章节的最大次数
    click_pause: tuple = (0.6, 1.4)     # 每次点击前随机停顿区间
    after_click_pause: tuple = (0.8, 2.0)  # 点击后随机停顿区间
    prompt_quiz: bool = True            # 遇到章节测验时暂停等待人工完成
    mute_when_possible: bool = True     # 尝试点击音量图标静音
    max_chapters: int = 0               # 0 表示不限制，调试时可设小值
    dry_run: bool = False               # 只识别不点击
    start_chapter: str = ""             # 指定起始章节号，如 "1.16"

    # ---------- 点击后校验（每次点击后立即做一次图像识别） ----------
    verify_delay: float = 1.0           # 点击后等待画面稳定的时间，再做识别
    verify_max_retry: int = 2           # 点击后页面无变化时的重试次数

    # ---------- “真的在播放”判定（画面运动 + 大播放按钮 + 播放键） ----------
    motion_interval: float = 0.55       # 采样两帧之间的间隔（秒）
    motion_threshold: float = 0.06      # 画面变化像素占比达到该值才认为“在动”
    motion_diff_pixel: int = 18         # 单像素差异阈值
    confirm_play_attempts: int = 6      # 确认播放的最大尝试次数
    confirm_play_timeout: float = 45.0  # 确认播放的最长等待时间（秒）

    # ---------- 断点续播 ----------
    resume_enabled: bool = True         # 是否记录并在下次启动时从上次章节继续

    # ---------- 日志 ----------
    save_error_screenshots: bool = True
    console_font: str = "Microsoft YaHei"

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        path = os.path.join(data_dir(), "config.json")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    raw = json.load(f)
                for k, v in raw.items():
                    if hasattr(cfg, k):
                        if isinstance(getattr(cfg, k), tuple) and isinstance(v, list):
                            v = tuple(v)
                        setattr(cfg, k, v)
            except Exception:
                pass
        else:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)
                    f.write("\n")
            except Exception:
                pass
        return cfg
