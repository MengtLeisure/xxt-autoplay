# -*- coding: utf-8 -*-
"""视频播放控制。

本版重点（第二轮迭代）：
    1. 每次点击都通过 ClickVerifier 执行 —— 点击后立即截图 + OCR 校验；
    2. 用“三信号”判断视频是否真的在播放：
         · 画面运动（两/三帧截图差异，不依赖控制条是否显示）
         · 中央大播放按钮是否存在（存在=还没开始播）
         · 控制条播放/暂停图标状态
       只有三者确认“真的在播”，才进入抖动悬停与进度监控；
    3. 进度卡住时不再盲目点击画面，而是先做一次完整的“确认真实播放”流程。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple

from . import win32
from .analyzer import PageAnalyzer
from .config import Config
from .errors import StopRequested
from .interaction import ClickVerifier
from .logger import Logger
from .model import PageState, PlayState
from .mouse import HumanMouse
from .screen import frame_motion


@dataclass
class PlaybackProbe:
    """一次“是否真的在播放”的探测结果。"""
    playing: bool
    motion: float
    center_play: bool
    play_state: str
    bar_row_y: int = 0

    def brief(self) -> str:
        return (f"运动={self.motion:.1%} 中央播放键={self.center_play} "
                f"播放键状态={self.play_state} 进度条行={self.bar_row_y}")


class ChapterVideoPlayer:
    """负责当前这一节视频的启动、播放确认与进度监控。"""

    def __init__(self, config: Config, logger: Logger, mouse: HumanMouse,
                 analyzer: PageAnalyzer, verifier: ClickVerifier):
        self.cfg = config
        self.logger = logger
        self.mouse = mouse
        self.analyzer = analyzer
        self.verifier = verifier

    # ------------------------------------------------------------------ 悬停
    def hover(self, state: PageState) -> None:
        """在视频画面上轻微抖动鼠标，让播放器控制条保持显示。"""
        point = state.video.hover_point
        if not point:
            return
        window = self.analyzer.window
        sx, sy = window.to_screen(*point)
        self.mouse.jitter(sx, sy, self.cfg.hover_jitter)

    # ------------------------------------------------------------------ 点击
    def _click_video_center(self, state: PageState,
                            purpose: str = "视频画面中央") -> Optional[PageState]:
        if state.video.video_rect is not None:
            rect = state.video.video_rect
            point = (rect.cx, rect.cy)
        elif state.video.hover_point:
            point = state.video.hover_point
        else:
            point = (max(50, state.sidebar_left // 2),
                     int(self.analyzer.window.rect.height * 0.5))
        return self.verifier.click(self.analyzer.window, *point, purpose=purpose)

    def _click_play_icon(self, state: PageState) -> Optional[PageState]:
        icon = state.video.icon_center
        if icon is None:
            return self._click_video_center(state, "画面中央（播放键位置未知）")
        return self.verifier.click(self.analyzer.window, *icon,
                                   purpose="控制条上的播放键")

    # -------------------------------------------------------- 播放真实性探测
    def probe(self, state: PageState) -> PlaybackProbe:
        """连续抓 3 帧，综合判断视频画面是否真的在动。"""
        window = self.analyzer.window
        f1 = window.capture()
        time.sleep(self.cfg.motion_interval)
        f2 = window.capture()
        time.sleep(self.cfg.motion_interval)
        f3 = window.capture()
        if f1 is None or f2 is None or f3 is None:
            return PlaybackProbe(False, 0.0, False, state.video.play_state, 0)

        # 顺便用像素刷新一次进度条/播放键状态
        try:
            self.analyzer.parser.refresh_video(f3, state)
        except Exception:  # noqa: BLE001
            pass

        rect = state.video.video_rect
        if rect is None:
            black = self.analyzer.parser.find_black_video_rect(
                f3, max(10, state.sidebar_left - 8))
            if black is not None:
                rect = black
                state.video.video_rect = black

        motion = 0.0
        if rect is not None:
            m1 = frame_motion(f1.pixels, f2.pixels, rect,
                              self.cfg.motion_diff_pixel, exclude_bottom=70)
            m2 = frame_motion(f2.pixels, f3.pixels, rect,
                              self.cfg.motion_diff_pixel, exclude_bottom=70)
            motion = min(m1, m2)
        else:
            m1 = frame_motion(f1.pixels, f2.pixels, None, self.cfg.motion_diff_pixel)
            m2 = frame_motion(f2.pixels, f3.pixels, None, self.cfg.motion_diff_pixel)
            motion = min(m1, m2)

        center_play = self.analyzer.parser.detect_center_play_button(
            f3, max(10, state.sidebar_left - 8))
        state.video.center_play = center_play
        play_state = state.video.play_state

        playing = False
        if not center_play and play_state != PlayState.PAUSED:
            if motion >= self.cfg.motion_threshold:
                playing = True
            elif play_state == PlayState.PLAYING and state.video.time_total > 0:
                # 画面本身很静（例如 PPT 视频），但控制条明确显示“暂停键”且有时间显示
                playing = True
        return PlaybackProbe(playing, motion, center_play, play_state,
                             state.video.bar_row_y)

    # ------------------------------------------------------------ 保证播放
    def ensure_playing(self, state: PageState) -> PageState:
        """确认视频真的在播放；没有的话通过“点击 → 识别校验”反复纠正。"""
        window = self.analyzer.window
        window.ensure_foreground()
        deadline = time.time() + self.cfg.confirm_play_timeout
        for attempt in range(1, self.cfg.confirm_play_attempts + 1):
            if win32.escape_pressed():
                raise StopRequested()
            probe = self.probe(state)
            if probe.playing:
                self.logger.ok(f"已确认视频真的在播放（{probe.brief()}）")
                return state
            self.logger.warn(f"第 {attempt} 次确认：尚未播放（{probe.brief()}）")

            if probe.center_play or probe.bar_row_y == 0:
                new_state = self._click_video_center(state, "画面中央的大播放按钮")
            elif probe.play_state == PlayState.PAUSED:
                new_state = self._click_play_icon(state)
            else:
                new_state = self._click_video_center(state, "画面中央（尝试开始播放）")
            if new_state is not None:
                state = new_state
            time.sleep(0.6)
            if time.time() > deadline:
                break

        probe = self.probe(state)
        if probe.playing:
            self.logger.ok(f"已确认视频真的在播放（{probe.brief()}）")
        else:
            self.logger.warn("多次校验后仍未确认播放，进入监控模式（之后会自动重试）")
        return state

    # ------------------------------------------------------------ 等待播完
    def wait_completed(self, state: PageState) -> Tuple[str, PageState]:
        window = self.analyzer.window
        self.logger.info(f"开始观看：{state.chapter_title or '(未知章节)'}")
        start = time.time()
        last_ratio = self._progress(state) or 0.0
        last_progress = start
        last_status = start
        stall_stage = 0
        unknown_ocr = 0
        while True:
            if win32.escape_pressed():
                raise StopRequested()
            window.ensure_foreground()
            # 只有确认已经在播放之后才做抖动悬停，避免误触发/误判
            self.hover(state)
            time.sleep(0.25)
            frame = self.analyzer.scan_fast(state)
            now = time.time()
            if frame is None:
                time.sleep(1.0)
                continue

            # 定期做一次全量 OCR，刷新时间/弹窗/测验/标题
            if now - self.analyzer.last_ocr_time >= self.cfg.ocr_interval:
                doc = self.analyzer.ocr.recognize(frame)
                if doc is not None:
                    state = self.analyzer.parser.parse(frame, doc, previous=state)
                    self.analyzer.last_state = state
                    self.analyzer.last_ocr_time = now
                    if state.quiz is not None:
                        return "quiz", state
                    if state.popup is not None and state.popup.button is not None:
                        new_state = self.verifier.click_line(
                            window, state.popup.button.line,
                            purpose=f"关闭弹窗：{state.popup.text}")
                        if new_state is not None:
                            state = new_state
                        unknown_ocr = 0
                        continue

            ratio = self._progress(state)
            if ratio is not None:
                unknown_ocr = 0
                if ratio >= self.cfg.completion_ratio:
                    self.logger.ok(
                        f"本节已播完（进度 {ratio * 100:.1f}%）："
                        f"{state.video.time_text}，用时 {self._duration(start)}")
                    return "completed", state
                if ratio > last_ratio + 0.002 or ratio < last_ratio - 0.02:
                    last_ratio = ratio
                    last_progress = now
                    stall_stage = 0

            if state.video.play_state == PlayState.PAUSED:
                self.logger.info("控制条显示视频已暂停，点击播放键")
                new_state = self._click_play_icon(state)
                if new_state is not None:
                    state = new_state
                last_progress = time.time()
                continue

            if now - last_status >= 12:
                last_status = now
                ratio_txt = f"{ratio * 100:.1f}%" if ratio is not None else "未知"
                self.logger.info(
                    f"进度 {ratio_txt}  {state.video.time_text}  "
                    f"已用时 {self._duration(start)}")

            idle = now - last_progress
            if idle > self.cfg.progress_stall_warn and stall_stage == 0:
                stall_stage = 1
                self.logger.warn(f"进度已 {idle:.0f} 秒没有变化，重新确认播放状态…")
                state = self.ensure_playing(state)
                last_progress = time.time()
            elif idle > self.cfg.progress_stall_recover and stall_stage <= 1:
                stall_stage = 2
                self.logger.warn("进度仍然不动，再次校验并尝试恢复播放…")
                state = self.ensure_playing(state)
                last_progress = time.time()
            elif idle > self.cfg.progress_stall_recover * 2 and stall_stage == 2:
                self.logger.error("视频长时间无法继续播放，跳过这一节")
                return "stalled", state

            # 完全识别不到进度条：很可能停在“大播放按钮”画面
            if ratio is None and state.video.bar_row_y == 0:
                unknown_ocr += 1
                if unknown_ocr >= 2:
                    self.logger.warn("识别不到播放进度，重新确认视频是否真的在播放…")
                    state = self.ensure_playing(state)
                    unknown_ocr = 0
                    _, refreshed = self.analyzer.scan(use_cache=False)
                    if refreshed is not None:
                        state = refreshed
            time.sleep(self.cfg.poll_interval)

    @staticmethod
    def _progress(state: PageState):
        vals = []
        if state.video.time_total > 0:
            vals.append(state.video.time_current / state.video.time_total)
        if state.video.bar_fill is not None:
            vals.append(state.video.bar_fill)
        if not vals:
            return None
        return max(vals)

    @staticmethod
    def _duration(start: float) -> str:
        sec = int(time.time() - start)
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}小时{m}分{s}秒"
        if m:
            return f"{m}分{s}秒"
        return f"{s}秒"
