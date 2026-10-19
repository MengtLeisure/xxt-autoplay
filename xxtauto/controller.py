# -*- coding: utf-8 -*-
"""主控制器：窗口定位 → 页面识别 → 播放确认 → 播放监控 → 切换章节。

第三轮迭代要点：
    * 所有点击统一交给 ClickVerifier：点完立即截图 + OCR 校验；
    * 只有“真的在播放”才进入抖动/监控（见 ChapterVideoPlayer.probe）；
    * 支持断点续播（ResumeStore）；
    * 失败时按“重试 → 降级（按钮/目录/滚动）”处理。
"""
from __future__ import annotations

import sys
import time
from typing import Dict, List, Optional

from . import win32
from .analyzer import PageAnalyzer
from .audio import SystemAudio
from .config import Config
from .errors import StopRequested
from .interaction import ClickVerifier
from .logger import Logger
from .model import PageState, SidebarItem
from .mouse import HumanMouse
from .navigator import ChapterNavigator
from .overlays import OverlayHandler
from .player import ChapterVideoPlayer
from .resume import ResumeStore
from .window import BrowserWindow


class AutoplayController:
    def __init__(self, config: Config, logger: Logger):
        self.cfg = config
        self.logger = logger
        self.window: Optional[BrowserWindow] = None
        self.analyzer: Optional[PageAnalyzer] = None
        self.mouse: Optional[HumanMouse] = None
        self.verifier: Optional[ClickVerifier] = None
        self.overlays: Optional[OverlayHandler] = None
        self.player: Optional[ChapterVideoPlayer] = None
        self.navigator: Optional[ChapterNavigator] = None
        self.audio: Optional[SystemAudio] = None
        self.resume = ResumeStore(logger)
        self.played: List[Dict] = []
        self.visited: set = set()
        self.current_state: Optional[PageState] = None

    # ------------------------------------------------------------------ 启动
    def run(self) -> int:
        self._banner()
        self.window = self._acquire_window()
        if self.window is None:
            self.logger.error("没有找到学习通页面所在的浏览器窗口")
            self.logger.info("请先在 Edge 中打开学习通课程播放页面（标题包含“学生学习页面”）")
            return 2
        win32.set_console_title("学习通自动播放 - 运行中（Esc 停止）")
        self.window.activate(maximize=True)
        self.analyzer = PageAnalyzer(self.cfg, self.logger, self.window)
        self.mouse = HumanMouse(self.cfg, self.logger)
        self.verifier = ClickVerifier(self.cfg, self.logger, self.mouse, self.analyzer)
        self.overlays = OverlayHandler(self.cfg, self.logger, self.mouse,
                                       self.analyzer, self.verifier)
        self.player = ChapterVideoPlayer(self.cfg, self.logger, self.mouse,
                                         self.analyzer, self.verifier)
        self.navigator = ChapterNavigator(self.cfg, self.logger, self.mouse,
                                          self.analyzer, self.verifier)
        self.audio = SystemAudio(self.logger)

        try:
            if self.cfg.mute_when_possible:
                if self.audio.mute():
                    self.logger.ok("已静音观看（程序退出时恢复原音量状态）")
            state = self.analyzer.wait_for_page(timeout=180)
            if state is None:
                self.logger.error("无法识别到课程播放页面，请检查浏览器窗口后重试")
                return 3
            return self._main_loop(state)
        except StopRequested:
            self.logger.warn("已按 Esc 手动停止")
            return 0
        except KeyboardInterrupt:
            self.logger.warn("已中断（Ctrl+C）")
            return 0
        finally:
            self._save_resume()
            if self.audio is not None:
                self.audio.restore()
            win32.set_console_title("学习通自动播放 - 已结束")
            self._summary()

    # ------------------------------------------------------------------ 主循环
    def _main_loop(self, state: PageState) -> int:
        assert self.analyzer and self.player and self.navigator and self.overlays
        saved = self.resume.load() if self.cfg.resume_enabled else None
        start_item = self._pick_start(state, saved)
        if start_item is None:
            self.logger.error("目录里没有识别到任何章节，无法开始")
            return 4
        current = start_item.number
        self.logger.section("任务计划")
        self.logger.info(f"起始章节：{start_item.display}")
        if saved:
            self.logger.info(f"断点续播记录：上次停在第 {saved.get('chapter_number')} 节"
                             f"（{saved.get('chapter_title', '')}）")
        if len(state.items) >= 2:
            self.logger.info(f"当前页面共识别到 {len(state.items)} 个章节："
                             f"{state.items[0].number} … {state.items[-1].number}")
        self.logger.info("规则：进度 ≥ "
                         f"{self.cfg.completion_ratio * 100:.0f}% 自动切下一节；"
                         "逐节向下；目录见底自动滚动；遇到章节测验会暂停等人工完成")
        self.logger.info("每次点击后都会自动做一次图像识别校验；"
                         "只有确认视频真的在播放才进入抖动监控")

        # 如果起始章节不是当前高亮章节，先点过去（点击后自动校验）
        if start_item.number != (state.active_item.number if state.active_item else ""):
            self.logger.info(f"正在打开起始章节：{start_item.display}")
            new_state = self.verifier.click_line(
                self.window, start_item.line, purpose=f"打开起始章节 {start_item.number}")
            if not self._title_changed(state, new_state):
                waited = self.navigator._wait_switch(state.chapter_title, state)  # noqa: SLF001
                if waited is not None:
                    new_state = waited
            if new_state is not None:
                state = new_state

        guard = 0
        while True:
            guard += 1
            self.current_state = state
            if guard > 800:
                self.logger.error("循环次数异常，停止以避免死循环")
                return 5
            if win32.escape_pressed():
                raise StopRequested()

            # 当前页面若是章节测验：暂停等人工完成，然后切到下一节
            if state.quiz is not None:
                self.overlays.wait_manual_quiz(state)
                new_state, how = self.navigator.go_next(state, current, self.visited)
                if how == "end":
                    self._finish_course()
                    return 0
                if new_state is None:
                    self.logger.warn("测验后切换下一节失败，重试一次")
                    time.sleep(3)
                    new_state, how = self.navigator.go_next(state, current, self.visited)
                if new_state is None:
                    self.logger.error("测验后无法切换到下一节，程序停止")
                    return 6
                state = new_state
                current = state.active_item.number if state.active_item else current
                continue

            # 1) 确认视频真的开始播放（内部：点击→识别→三信号校验）
            state = self.player.ensure_playing(state)
            self.current_state = state
            # 2) 监控进度直到播完
            result, state = self.player.wait_completed(state)
            self.current_state = state

            if result == "quiz":
                self.logger.warn("视频页面之后是章节测验，等待人工完成后继续")
                self.overlays.wait_manual_quiz(state)
                new_state, how = self.navigator.go_next(state, current, self.visited)
                if how == "end":
                    self._finish_course()
                    return 0
                if new_state is None:
                    self.logger.error("测验后无法切换到下一节，程序停止")
                    return 6
                state = new_state
                current = state.active_item.number if state.active_item else current
                continue
            if result == "stalled":
                self.logger.warn("本节视频卡住，先记录并尝试切换下一节")
            self._record(state, current)

            if win32.escape_pressed():
                raise StopRequested()
            number = state.active_item.number if state.active_item else current
            new_state, how = self.navigator.go_next(state, number, self.visited)
            if how == "end":
                self._finish_course()
                return 0
            if new_state is None:
                self.logger.warn("切换下一节失败，3 秒后重试一次")
                time.sleep(3)
                new_state, how = self.navigator.go_next(state, number, self.visited)
            if new_state is None:
                self.logger.error("多次切换下一节失败，程序停止。可查看日志截图定位原因")
                return 6
            state = new_state
            current = state.active_item.number if state.active_item else number
            if self.cfg.max_chapters and len(self.played) >= self.cfg.max_chapters:
                self.logger.ok(f"已达到调试上限 {self.cfg.max_chapters} 节，停止")
                return 0

    # ------------------------------------------------------------------ 选择
    def _pick_start(self, state: PageState,
                    saved: Optional[Dict]) -> Optional[SidebarItem]:
        if not state.items:
            return None
        if self.cfg.start_chapter:
            wanted = state.item_by_number(self.cfg.start_chapter)
            if wanted is not None:
                return wanted
            self.logger.warn(f"目录中找不到指定章节 {self.cfg.start_chapter}，改用当前高亮章节")
        active = state.active_item
        # 断点续播：如果记录的章节比当前高亮更靠后，就从记录处继续
        if saved and saved.get("chapter_number"):
            target = state.item_by_number(str(saved["chapter_number"]))
            if target is not None:
                if active is None or target.number_tuple() > active.number_tuple():
                    return target
                self.logger.info("断点记录的章节不比当前高亮靠后，按当前高亮继续")
        if active is not None:
            return active
        if state.chapter_title:
            matched = self.analyzer.parser._match_title(state.items, state.chapter_title)  # noqa: SLF001
            if matched is not None:
                return matched
        return state.items[0]

    @staticmethod
    def _title_changed(old_state: PageState, new_state: Optional[PageState]) -> bool:
        if new_state is None:
            return False
        if new_state.chapter_title and new_state.chapter_title != old_state.chapter_title:
            return True
        old_num = old_state.active_item.number if old_state.active_item else ""
        new_num = new_state.active_item.number if new_state.active_item else ""
        return bool(new_num and new_num != old_num)

    # ------------------------------------------------------------------ 记录
    def _record(self, state: PageState, current: str) -> None:
        number = state.active_item.number if state.active_item else current
        self.visited.add(number)
        ratio = state.video.ratio
        if state.video.bar_fill is not None:
            ratio = state.video.bar_fill if ratio is None else max(ratio, state.video.bar_fill)
        entry = {
            "number": number,
            "title": state.chapter_title or (state.active_item.title if state.active_item else ""),
            "ratio": ratio,
            "time": state.video.time_text,
        }
        self.played.append(entry)
        pct = f"{ratio * 100:.1f}%" if ratio is not None else "未知"
        self.logger.ok(f"已记录第 {len(self.played)} 节：{number} {entry['title']}（{pct}）")
        self._save_resume()

    # ------------------------------------------------------------------ 续播
    def _save_resume(self) -> None:
        if not (self.cfg.resume_enabled and self.current_state is not None):
            return
        state = self.current_state
        number = state.active_item.number if state.active_item else ""
        if not number:
            return
        self.resume.save(number, state.chapter_title, sorted(self.visited))

    def _finish_course(self) -> None:
        self.logger.ok("已经是本课程的最后一节，全部播放任务完成！")
        try:
            self.resume.clear()
        except Exception:
            pass

    # ------------------------------------------------------------------ 收尾
    def _banner(self) -> None:
        self.logger.section("学习通自动播放（图像识别版）")
        self.logger.info("运行逻辑：截屏识别页面 → 模拟鼠标点击 → 每次点击后识别校验 → "
                         "确认真的在播放 → 监控进度 → 自动切换下一节")
        self.logger.info(f"日志目录：{self.logger.run_dir}")
        self.logger.info("查看屏幕顶部提示：任意时刻按 Esc 可安全停止")

    def _acquire_window(self) -> Optional[BrowserWindow]:
        self.logger.info("正在查找浏览器窗口…")
        for attempt in range(30):
            window = BrowserWindow.locate(self.cfg, self.logger)
            if window is not None:
                return window
            if attempt == 0:
                self.logger.warn("暂未找到学习通页面窗口，请在 Edge 中打开课程页面；程序会继续等待…")
            time.sleep(2)
        return None

    def _summary(self) -> None:
        if not self.played:
            return
        self.logger.section("本次播放汇总")
        for i, entry in enumerate(self.played, 1):
            ratio = entry["ratio"]
            pct = f"{ratio * 100:.1f}%" if ratio is not None else "未知"
            self.logger.info(f"{i:02d}. {entry['number']} {entry['title']}  进度 {pct}  {entry['time']}")
        self.logger.info(f"共播放 {len(self.played)} 节，日志：{self.logger.log_path}")
        if self.verifier is not None:
            self.logger.info(f"本次共执行 {self.verifier.count} 次点击，"
                             f"每次都做了图像识别校验")
