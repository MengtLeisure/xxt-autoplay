# -*- coding: utf-8 -*-
"""学习通自动播放（图像识别版）入口。

用法：
    双击运行（桌面 exe）          直接开始
    python main.py --dry-run      只识别当前页面并打印结果，不点击（调试用）
    python main.py --image 图片   识别指定截图并打印结果（离线调试用）
    python main.py --start 1.16   从指定章节开始
    python main.py --verbose      输出更多调试信息
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from xxtauto import win32
from xxtauto.analyzer import PageAnalyzer
from xxtauto.config import Config
from xxtauto.controller import AutoplayController
from xxtauto.logger import Logger
from xxtauto.model import PlayState
from xxtauto.ocr import OcrEngine
from xxtauto.parser import XxtPageParser
from xxtauto.resume import ResumeStore
from xxtauto.screen import ScreenCapture
from xxtauto.window import BrowserWindow


def _setup_console(cfg: Config) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    win32.set_console_font(cfg.console_font, 16)
    win32.set_console_title("学习通自动播放（图像识别版）")


def _print_state(state, parser=None) -> None:
    print("=" * 64)
    print("识别结果")
    print("=" * 64)
    print(f"是否学习通页面 : {state.is_xxt_page}")
    print(f"布局是否就绪   : {state.layout_ready}")
    print(f"章节标题       : {state.chapter_title}")
    print(f"侧边栏左边界   : {state.sidebar_left}px")
    print(f"识别到章节数   : {len(state.items)}")
    for i, item in enumerate(state.items):
        mark = " ← 当前播放" if (state.active_item and item.number == state.active_item.number) else ""
        print(f"   {i + 1:02d}. {item.number:>6s}  {item.title}{mark}")
    if state.active_item:
        print(f"当前高亮章节   : {state.active_item.number} {state.active_item.title}")
    else:
        print("当前高亮章节   : 未识别到")
    vp = state.video
    ratio = vp.ratio
    print(f"播放状态       : {vp.play_state}")
    print(f"画面中央大播放键: {vp.center_play}")
    print(f"视频时间       : {vp.time_text}  进度={ratio if ratio is None else round(ratio * 100, 1)}%")
    print(f"进度条矩形     : {vp.bar_rect}   进度条填充={vp.bar_fill}")
    print(f"视频区域       : {vp.video_rect}   悬停点={vp.hover_point}   播放键={vp.icon_center}")
    print(f"下一节按钮     : {state.next_button.text if state.next_button else '未识别到'}")
    print(f"弹窗           : {state.popup.text if state.popup else '无'}")
    print(f"章节测验       : {state.quiz.text if state.quiz else '无'}")
    print("=" * 64)


def _analyze_image(image_path: str, cfg: Config, logger: Logger, verbose: bool):
    frame = ScreenCapture.grab_image(image_path)
    ocr = OcrEngine(logger=logger)
    doc = ocr.recognize(frame, use_cache=False)
    if doc is None:
        logger.error("OCR 识别失败")
        return 1
    state = XxtPageParser(cfg, logger).parse(frame, doc)
    _print_state(state)
    if verbose:
        print("\n--- OCR 原始行 ---")
        for ln in doc.lines:
            print(f"({ln.x0:4d},{ln.y0:4d})-({ln.x1:4d},{ln.y1:4d})  {ln.text}")
    # 输出一张标注图，便于核对识别结果
    try:
        from PIL import ImageDraw
        img = frame.image.copy()
        draw = ImageDraw.Draw(img)
        for item in state.items:
            color = (0, 200, 0) if state.active_item and item.number == state.active_item.number else (0, 120, 255)
            draw.rectangle([item.line.x0, item.line.y0, item.line.x1, item.line.y1], outline=color, width=2)
        if state.video.bar_rect:
            r = state.video.bar_rect
            draw.rectangle([r.x0, r.y0, r.x1, r.y1], outline=(255, 0, 0), width=2)
        if state.next_button:
            ln = state.next_button.line
            draw.rectangle([ln.x0, ln.y0, ln.x1, ln.y1], outline=(255, 140, 0), width=2)
        out_dir = logger.run_dir
        out_path = os.path.join(out_dir, "annotated.png")
        img.save(out_path)
        print(f"标注图已保存：{out_path}")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"标注图保存失败：{exc}")
    return 0


def _dry_run_live(cfg: Config, logger: Logger) -> int:
    logger.section("试运行（只识别，不点击）")
    window = BrowserWindow.locate(cfg, logger)
    if window is None:
        logger.error("未找到浏览器窗口")
        return 2
    # 试运行时先把浏览器窗口激活到前台，避免截图被其他窗口遮挡
    window.activate(maximize=True)
    time.sleep(1.0)
    frame = window.capture()
    if frame is None:
        logger.error("截图失败")
        return 2
    ocr = OcrEngine(logger=logger)
    doc = ocr.recognize(frame, use_cache=False)
    if doc is None:
        logger.error("OCR 识别失败")
        return 2
    state = XxtPageParser(cfg, logger).parse(frame, doc)
    _print_state(state)
    logger.info(f"截图已保存：{logger.save_shot(frame.image, 'dryrun')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="学习通自动播放（图像识别版）")
    parser.add_argument("--image", help="离线识别指定截图，不进行任何点击")
    parser.add_argument("--dry-run", action="store_true", help="只识别当前页面，不点击")
    parser.add_argument("--start", help="从指定章节号开始，例如 1.16")
    parser.add_argument("--max-chapters", type=int, default=0, help="最多播放几节（调试用）")
    parser.add_argument("--verbose", action="store_true", help="输出调试信息")
    parser.add_argument("--no-pause", action="store_true", help="结束后不等待回车")
    parser.add_argument("--no-countdown", action="store_true", help="跳过启动倒计时")
    parser.add_argument("--reset-resume", action="store_true",
                        help="清除断点续播记录，从头按页面当前章节开始")
    args = parser.parse_args(argv)

    win32.enable_dpi_awareness()
    cfg = Config.load()
    _setup_console(cfg)
    if args.verbose:
        cfg_verbose = True
    else:
        cfg_verbose = False
    if args.start:
        cfg.start_chapter = args.start
    if args.max_chapters:
        cfg.max_chapters = args.max_chapters
    logger = Logger(verbose=args.verbose)
    if args.reset_resume:
        ResumeStore(logger).clear()
        logger.info("已清除断点续播记录")

    try:
        if args.image:
            if not os.path.isfile(args.image):
                logger.error(f"找不到图片：{args.image}")
                return 2
            return _analyze_image(args.image, cfg, logger, args.verbose)
        if args.dry_run:
            return _dry_run_live(cfg, logger)

        logger.section("准备开始自动播放")
        logger.info("请保持 Edge 窗口不要最小化；程序会自动激活窗口并模拟鼠标操作")
        logger.info("任务进行中请勿移动鼠标/键盘，按 Esc 可随时停止")
        if not args.no_countdown:
            logger.info("6 秒后开始…（按 Esc 取消）")
            for i in range(6, 0, -1):
                if win32.escape_pressed():
                    logger.warn("已取消")
                    return 0
                print(f"\r  倒计时 {i} …", end="", flush=True)
                time.sleep(1.0)
            print("\r" + " " * 24 + "\r", end="")

        controller = AutoplayController(cfg, logger)
        code = controller.run()
        return code
    finally:
        logger.close()
        frozen = bool(getattr(sys, "frozen", False))
        if frozen and not args.no_pause:
            try:
                input("\n按回车键关闭窗口…")
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
