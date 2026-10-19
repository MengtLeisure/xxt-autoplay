# -*- coding: utf-8 -*-
"""离线自检：用真实截图 + 人工合成的几种页面状态，验证识别逻辑。

运行：python tools/selftest.py [截图路径]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont

from xxtauto.config import Config
from xxtauto.logger import Logger
from xxtauto.ocr import OcrEngine
from xxtauto.parser import XxtPageParser
from xxtauto.screen import ScreenCapture


def load_font(size: int):
    for name in ("msyh.ttc", "simhei.ttf", "simsun.ttc"):
        path = os.path.join("C:\\Windows\\Fonts", name)
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


class Checker:
    def __init__(self):
        self.ok = 0
        self.fail = 0

    def check(self, name: str, cond: bool, detail: str = ""):
        if cond:
            self.ok += 1
            print(f"  [PASS] {name}  {detail}")
        else:
            self.fail += 1
            print(f"  [FAIL] {name}  {detail}")


def parse_image(path, cfg, logger, ocr, parser):
    frame = ScreenCapture.grab_image(path)
    doc = ocr.recognize(frame, use_cache=False)
    if doc is None:
        raise RuntimeError("OCR 失败")
    return frame, doc, parser.parse(frame, doc)


def main():
    image_path = sys.argv[1] if len(sys.argv) > 1 else r"..\edge-automation\user-shot.png"
    image_path = os.path.abspath(image_path)
    cfg = Config()
    logger = Logger(verbose=False)
    ocr = OcrEngine(logger=logger)
    parser = XxtPageParser(cfg, logger)
    checker = Checker()
    work = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp")
    os.makedirs(work, exist_ok=True)

    print(f"测试截图：{image_path}")
    frame, doc, state = parse_image(image_path, cfg, logger, ocr, parser)
    print("\n[1] 基础页面识别")
    checker.check("识别为学习通页面", state.is_xxt_page)
    checker.check("识别到章节列表", len(state.items) >= 10, f"共 {len(state.items)} 节")
    checker.check("识别到当前高亮章节", state.active_item is not None,
                  state.active_item.display if state.active_item else "无")
    checker.check("识别到播放进度", state.video.time_total > 0, state.video.time_text)
    checker.check("识别到进度条", state.video.bar_rect is not None, str(state.video.bar_rect))
    checker.check("识别到「下一节」按钮", state.next_button is not None)
    checker.check("当前页面没有误报弹窗", state.popup is None,
                  state.popup.text if state.popup else "")
    checker.check("当前页面没有误报测验", state.quiz is None,
                  state.quiz.text if state.quiz else "")

    # ---------------- [2] 播放器处于暂停态 ----------------
    print("\n[2] 暂停状态识别")
    img = frame.image.copy()
    draw = ImageDraw.Draw(img)
    if state.video.bar_rect:
        bar = state.video.bar_rect
        draw.rectangle([bar.x0 + 4, bar.y1 + 8, bar.x0 + 62, bar.y1 + 76], fill=(0, 0, 0))
        # 画一个播放三角
        draw.polygon([(bar.x0 + 16, bar.y1 + 20), (bar.x0 + 16, bar.y1 + 62),
                      (bar.x0 + 50, bar.y1 + 41)], fill=(255, 255, 255))
    paused_path = os.path.join(work, "paused.png")
    img.save(paused_path)
    _, _, paused_state = parse_image(paused_path, cfg, logger, ocr, parser)
    checker.check("识别出“已暂停”", paused_state.video.play_state == "paused",
                  paused_state.video.play_state)

    # ---------------- [3] 进度条填充 60% ----------------
    print("\n[3] 进度条填充比例识别")
    img = frame.image.copy()
    draw = ImageDraw.Draw(img)
    if state.video.bar_rect:
        bar = state.video.bar_rect
        end = bar.x0 + int(bar.width * 0.6)
        draw.rectangle([bar.x0, bar.y0 - 1, end, bar.y1 + 1], fill=(58, 139, 255))
        draw.rectangle([end, bar.y0 - 4, end + 6, bar.y1 + 4], fill=(241, 241, 241))
    filled_path = os.path.join(work, "filled60.png")
    img.save(filled_path)
    _, _, fill_state = parse_image(filled_path, cfg, logger, ocr, parser)
    fill = fill_state.video.bar_fill or 0
    checker.check("进度条比例≈60%", abs(fill - 0.6) <= 0.08, f"识别为 {fill * 100:.1f}%")

    # ---------------- [4] 弹窗识别 ----------------
    print("\n[4] 弹窗识别")
    img = frame.image.copy()
    draw = ImageDraw.Draw(img)
    font_big = load_font(30)
    font_btn = load_font(28)
    draw.rounded_rectangle([520, 420, 1400, 900], radius=16, fill=(255, 255, 255),
                           outline=(200, 200, 200), width=2)
    draw.text((580, 470), "提示：视频播放完毕，是否继续下一节？", font=font_big, fill=(60, 60, 60))
    draw.rounded_rectangle([1000, 760, 1160, 830], radius=8, fill=(64, 140, 255))
    draw.text((1040, 775), "确定", font=font_btn, fill=(255, 255, 255))
    popup_path = os.path.join(work, "popup.png")
    img.save(popup_path)
    _, _, popup_state = parse_image(popup_path, cfg, logger, ocr, parser)
    checker.check("识别出弹窗", popup_state.popup is not None,
                  popup_state.popup.text if popup_state.popup else "无")
    checker.check("识别出弹窗按钮", bool(popup_state.popup and popup_state.popup.button),
                  popup_state.popup.button.text if (popup_state.popup and popup_state.popup.button) else "")

    # ---------------- [5] 章节测验识别 ----------------
    print("\n[5] 章节测验识别")
    img = frame.image.copy()
    draw = ImageDraw.Draw(img)
    draw.text((600, 380), "单选题（每题 2 分）", font=font_big, fill=(40, 40, 40))
    draw.text((600, 450), "1. 现代国防的核心是什么？", font=font_big, fill=(40, 40, 40))
    quiz_path = os.path.join(work, "quiz.png")
    img.save(quiz_path)
    _, _, quiz_state = parse_image(quiz_path, cfg, logger, ocr, parser)
    checker.check("识别出章节测验", quiz_state.quiz is not None,
                  quiz_state.quiz.text if quiz_state.quiz else "无")

    # ---------------- [6] 未开始播放：画面中央大播放按钮 ----------------
    print("\n[6] “未开始播放”画面识别（中央大播放按钮）")
    img = frame.image.copy()
    draw = ImageDraw.Draw(img)
    vx0, vy0, vx1, vy1 = 150, 400, 1700, 1400
    draw.rectangle([vx0, vy0, vx1, vy1], fill=(0, 0, 0))
    ccx, ccy = (vx0 + vx1) // 2, (vy0 + vy1) // 2
    draw.ellipse([ccx - 46, ccy - 46, ccx + 46, ccy + 46], fill=(120, 120, 120))
    draw.polygon([(ccx - 14, ccy - 22), (ccx - 14, ccy + 22), (ccx + 24, ccy)],
                 fill=(245, 245, 245))
    notstarted_path = os.path.join(work, "notstarted.png")
    img.save(notstarted_path)
    _, _, notstarted_state = parse_image(notstarted_path, cfg, logger, ocr, parser)
    checker.check("识别出“中央大播放按钮”", notstarted_state.video.center_play,
                  f"center_play={notstarted_state.video.center_play}")
    checker.check("未开始时没有误判为播放中",
                  notstarted_state.video.play_state != "playing",
                  notstarted_state.video.play_state)

    # ---------------- [7] 画面运动检测（判断视频是否真的在动） ----------------
    print("\n[7] 画面运动检测")
    from xxtauto.screen import frame_motion
    import numpy as np
    px = frame.pixels
    rect = state.video.video_rect
    if rect is not None:
        shifted = px.copy()
        y0, y1 = max(0, rect.y0), min(px.shape[0], rect.y1)
        x0, x1 = max(0, rect.x0), min(px.shape[1], rect.x1)
        band = shifted[y0:y1, x0:x1].copy()
        shifted[y0:y1, x0:x1] = np.roll(band, 6, axis=1)
        motion_moving = frame_motion(px, shifted, rect, 18, exclude_bottom=70)
        motion_static = frame_motion(px, px, rect, 18, exclude_bottom=70)
        checker.check("静止画面运动≈0", motion_static < 0.01, f"motion={motion_static:.4f}")
        checker.check("变化画面运动明显", motion_moving > 0.10, f"motion={motion_moving:.4f}")
    else:
        checker.check("视频区域识别", False, "未识别到 video_rect")

    print("\n=========================================")
    print(f"自检结果：通过 {checker.ok} 项，失败 {checker.fail} 项")
    print("=========================================")
    return 0 if checker.fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
