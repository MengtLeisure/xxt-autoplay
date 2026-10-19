# -*- coding: utf-8 -*-
"""图像文字识别（OCR）。

使用 Windows 自带的 OCR 引擎（中文简体），通过 PowerShell 调用：
  * 不需要安装 Tesseract，也不需要联网；
  * 输入是屏幕截图，输出是“文字 + 位置”，完全属于图像识别范畴。

对外提供 OcrEngine.recognize(frame) -> OcrDocument，以及按文字查找的便捷方法。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image

from .config import data_dir
from .screen import Frame

OCR_PS1 = r"""
param([string]$ImagePath, [string]$OutJson)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await($WinRtTask, $ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null
$bytes = [System.IO.File]::ReadAllBytes($ImagePath)
$ms = New-Object System.IO.MemoryStream(,$bytes)
$ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = $null
try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('zh-Hans-CN')) } catch { }
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { throw 'no OCR engine available' }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = New-Object System.Collections.ArrayList
foreach ($line in $result.Lines) {
  $words = New-Object System.Collections.ArrayList
  foreach ($w in $line.Words) {
    [void]$words.Add([pscustomobject]@{ t=$w.Text; x=[int]$w.BoundingRect.X; y=[int]$w.BoundingRect.Y; w=[int]$w.BoundingRect.Width; h=[int]$w.BoundingRect.Height })
  }
  [void]$lines.Add([pscustomobject]@{ t=$line.Text; words=$words })
}
$payload = [pscustomobject]@{ w=$bitmap.PixelWidth; h=$bitmap.PixelHeight; lines=$lines }
$payload | ConvertTo-Json -Depth 6 -Compress | Set-Content -LiteralPath $OutJson -Encoding UTF8
"""


@dataclass
class OcrWord:
    text: str
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


@dataclass
class OcrLine:
    text: str          # 已去掉空格，保留原标点
    words: List[OcrWord]
    x0: int = 0
    y0: int = 0
    x1: int = 0
    y1: int = 0

    def __post_init__(self):
        if self.words:
            self.x0 = min(w.x for w in self.words)
            self.y0 = min(w.y for w in self.words)
            self.x1 = max(w.x + w.w for w in self.words)
            self.y1 = max(w.y + w.h for w in self.words)

    @property
    def cx(self) -> int:
        return (self.x0 + self.x1) // 2

    @property
    def cy(self) -> int:
        return (self.y0 + self.y1) // 2

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    def contains_point(self, x: int, y: int, pad: int = 0) -> bool:
        return (self.x0 - pad <= x <= self.x1 + pad) and (self.y0 - pad <= y <= self.y1 + pad)


@dataclass
class OcrDocument:
    width: int
    height: int
    lines: List[OcrLine]
    scale: float = 1.0
    elapsed: float = 0.0

    def find(self, pattern: str, flags: int = 0) -> List[OcrLine]:
        rx = re.compile(pattern, flags)
        return [ln for ln in self.lines if rx.search(ln.text)]

    def find_exact(self, text: str) -> Optional[OcrLine]:
        for ln in self.lines:
            if ln.text == text:
                return ln
        return None

    def containing(self, text: str) -> List[OcrLine]:
        return [ln for ln in self.lines if text in ln.text]

    def in_region(self, x0: int, y0: int, x1: int, y1: int) -> List[OcrLine]:
        return [ln for ln in self.lines
                if x0 <= ln.cx <= x1 and y0 <= ln.cy <= y1]

    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)


def normalize_text(text: str) -> str:
    """去掉 OCR 在汉字之间插入的空格，统一中文标点。"""
    text = re.sub(r"\s+", "", text or "")
    trans = {ord("："): ":", ord("／"): "/", ord("．"): ".", ord("％"): "%",
             ord("（"): "(", ord("）"): ")", ord("，"): ",", ord("。"): "."}
    return text.translate(trans)


class OcrEngine:
    """调用 Windows OCR 的封装，带超时与失败重试。"""

    def __init__(self, logger=None, timeout: float = 25.0, downscale_limit: int = 3800):
        self.logger = logger
        self.timeout = timeout
        self.downscale_limit = downscale_limit
        self._work_dir = os.path.join(data_dir(), "ocr_tmp")
        os.makedirs(self._work_dir, exist_ok=True)
        self._script_path = self._ensure_script()
        self._tmp_dir = self._work_dir
        self._counter = 0
        self._cache_key: Optional[str] = None
        self._cache_doc: Optional[OcrDocument] = None
        self.last_duration: float = 0.0

    def _ensure_script(self) -> str:
        path = os.path.join(self._work_dir, "xxtauto_ocr_winrt.ps1")
        try:
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(OCR_PS1)
        except Exception:
            pass
        return path

    def _prepare_image(self, frame: Frame) -> Tuple[str, float]:
        img: Image.Image = frame.image
        scale = 1.0
        max_dim = max(img.width, img.height)
        if max_dim > self.downscale_limit:
            scale = self.downscale_limit / float(max_dim)
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                             Image.LANCZOS)
        self._counter += 1
        path = os.path.join(self._tmp_dir, f"shot_{self._counter % 5}.png")
        img.save(path, format="PNG", compress_level=1)
        return path, scale

    def recognize(self, frame: Frame, use_cache: bool = False) -> Optional[OcrDocument]:
        start = time.time()
        try:
            key = None
            if use_cache:
                key = hashlib.md5(frame.pixels.tobytes()).hexdigest()
                if key == self._cache_key and self._cache_doc is not None:
                    return self._cache_doc
            image_path, scale = self._prepare_image(frame)
            out_json = os.path.join(self._tmp_dir, "ocr_out.json")
            if os.path.exists(out_json):
                try:
                    os.remove(out_json)
                except Exception:
                    pass
            cmd = [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                "-File", self._script_path,
                "-ImagePath", image_path, "-OutJson", out_json,
            ]
            creationflags = 0x08000000  # CREATE_NO_WINDOW
            proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout,
                                  creationflags=creationflags)
            if not os.path.exists(out_json):
                if self.logger:
                    err = proc.stderr.decode("utf-8", "ignore")[:300]
                    self.logger.warn(f"OCR 失败：{err or '未生成结果文件'}")
                return None
            with open(out_json, "r", encoding="utf-8-sig") as f:
                raw = json.load(f)
            lines: List[OcrLine] = []
            for item in raw.get("lines", []) or []:
                words = [OcrWord(w.get("t", ""), int(w.get("x", 0)), int(w.get("y", 0)),
                                 int(w.get("w", 0)), int(w.get("h", 0)))
                         for w in (item.get("words") or [])]
                if not words:
                    continue
                text = normalize_text(item.get("t", ""))
                lines.append(OcrLine(text, words))
            doc = OcrDocument(int(raw.get("w", frame.width)), int(raw.get("h", frame.height)),
                              lines, scale=scale, elapsed=time.time() - start)
            self.last_duration = doc.elapsed
            if use_cache:
                self._cache_key, self._cache_doc = key, doc
            if self.logger:
                self.logger.debug(f"OCR {len(lines)} 行, 用时 {doc.elapsed:.2f}s")
            return doc
        except subprocess.TimeoutExpired:
            if self.logger:
                self.logger.warn("OCR 超时，跳过本次识别")
            return None
        except Exception as exc:  # noqa: BLE001
            if self.logger:
                self.logger.warn(f"OCR 异常：{exc}")
            return None

    def cleanup(self) -> None:
        try:
            for name in os.listdir(self._tmp_dir):
                os.remove(os.path.join(self._tmp_dir, name))
            os.rmdir(self._tmp_dir)
        except Exception:
            pass
