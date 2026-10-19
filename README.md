# 学习通自动播放（图像识别版）

用 **纯图像识别 + 模拟真人鼠标点击** 自动按顺序播放学习通课程视频的桌面程序。

不读取网页 DOM、不调用任何网页脚本接口、不修改页面数据 —— 工作方式和真人完全一致：
截屏 → OCR/像素分析认出目录与进度条 → 移动并点击鼠标 → 继续看屏幕。

> 面向对象 Python 实现，Windows 10/11，使用系统自带简体中文 OCR（无需安装 Tesseract）。

## 功能特性

- **图像识别驱动**：识别课程目录、当前高亮章节、播放时间、进度条、底部「下一节」按钮
- **按顺序播放**：进度（时间 + 进度条双信号）达到 95% 自动切下一节；目录见底自动向下滚动继续
- **每次点击后识别校验**：所有点击统一经过 `ClickVerifier`，点完立即截图 + OCR 校验，
  页面没反应就立刻降级重试（按钮 → 目录行 → 滚动 → 再试）
- **"真的在播放"三重判定**：画面运动 + 中央大播放按钮检测 + 控制条播放/暂停图标，
  修复"视频中央还停着播放键却被误判为播放中"的问题；确认后才开始抖动悬停
- **弹窗处理**：识别并关闭「确定 / 知道了 / 继续观看」等正常弹窗
- **章节测验暂停**：遇到测验时暂停自动化，等你手动完成后自动继续（可配置为自动跳过）
- **断点续播**：记录当前章节与已完成列表，下次启动自动接着播
- **拟人化操作**：贝塞尔曲线移动 + 随机停顿 + 微抖动，避免机械式点击
- **静音观看**：自动静音系统音量，退出时恢复

## 目录结构

```
xxt-autoplay/
├── main.py                  # 程序入口（命令行参数、试运行、倒计时）
├── readme.txt               # 详细中文使用说明
├── xxtauto/                 # 核心模块（面向对象）
│   ├── controller.py        # 主控制器
│   ├── player.py            # 播放确认与进度监控
│   ├── parser.py            # 页面图像解析
│   ├── interaction.py       # 点击校验
│   ├── navigator.py         # 章节切换与目录滚动
│   ├── ocr.py               # Windows OCR 封装
│   ├── screen.py            # 截图 / 画面运动检测
│   ├── mouse.py             # 拟人化鼠标
│   ├── win32.py             # Win32 API
│   ├── audio.py             # 系统静音控制
│   ├── resume.py            # 断点续播
│   ├── overlays.py          # 弹窗 / 测验处理
│   ├── model.py / config.py / logger.py / window.py / analyzer.py
├── tools/selftest.py        # 离线自检（17 项图像识别测试）
└── dist/学习通自动播放.exe   # 打包好的可执行程序（PyInstaller，免安装环境）
```

## 快速开始

1. 用 Microsoft Edge 打开学习通课程播放页（标题含「学生学习页面」），登录好；
2. 双击 `dist/学习通自动播放.exe`；
3. 6 秒倒计时后自动开始：程序会自动最大化浏览器、识别页面、播放视频、自动切下一节；
4. 任意时刻按 **Esc** 停止；结束时按回车关闭窗口。

更多说明见 [readme.txt](readme.txt)，包括参数调整、常见问题、调试命令：

```bash
学习通自动播放.exe --dry-run          # 只识别不点击，检查识别是否正常
学习通自动播放.exe --max-chapters 1   # 只播 1 节试跑
学习通自动播放.exe --start 1.16       # 从指定章节开始
学习通自动播放.exe --reset-resume     # 清除断点续播记录
```

## 从源码运行

```bash
pip install pillow numpy
python main.py
```

打包（可选）：

```bash
pip install pyinstaller
pyinstaller --onefile --console --name "学习通自动播放" main.py
```

## 配置

首次运行后生成：`%LOCALAPPDATA%\XxtAutoPlay\config.json`

常用参数：`completion_ratio`（默认 0.95）、`ocr_interval`、`verify_delay`、
`motion_threshold`、`resume_enabled`、`mute_when_possible`、`prompt_quiz` 等，
详见 readme.txt。

## 免责声明

本程序仅用于个人学习辅助。使用自动化工具可能违反平台或学校规定，风险与后果由使用者自行承担。
程序不收集、不上传任何数据，所有识别均在本机完成。
