# -*- coding: utf-8 -*-
"""系统音量控制（用于静音观看，退出时自动恢复）。

说明：这只是操作系统层面的音量开关，不涉及网页 DOM/脚本，
页面上的所有操作仍然完全依靠图像识别 + 模拟鼠标点击。
"""
from __future__ import annotations


class SystemAudio:
    """通过 Windows Core Audio API 读取/设置“静音”状态。"""

    CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
    IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
    IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"
    CLSCTX_ALL = 23

    def __init__(self, logger=None):
        self.logger = logger
        self._endpoint = None
        self._original_mute = None
        self._available = False
        try:
            self._init_com()
            self._available = True
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger.debug(f"音量控制不可用：{exc}")

    # ------------------------------------------------------------------ COM
    def _init_com(self):
        import ctypes
        from ctypes import wintypes

        ole32 = ctypes.WinDLL("ole32")
        self._ctypes = ctypes
        ole32.CoInitialize(None)

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        def guid(text: str) -> GUID:
            g = GUID()
            ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
            return g

        enumerator = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(guid(self.CLSID_MMDeviceEnumerator)), None,
                               self.CLSCTX_ALL, ctypes.byref(guid(self.IID_IMMDeviceEnumerator)),
                               ctypes.byref(enumerator))
        if not enumerator:
            raise RuntimeError("无法创建设备枚举器")
        device = ctypes.c_void_p()
        # GetDefaultAudioEndpoint(0=eRender, 0=eConsole)  vtable index 4
        self._call(enumerator, 4, ctypes.byref(device), ctypes.c_int(0), ctypes.c_int(0))
        if not device:
            raise RuntimeError("无法获取默认音频设备")
        endpoint = ctypes.c_void_p()
        # IMMDevice::Activate  vtable index 3
        self._call(device, 3, ctypes.byref(guid(self.IID_IAudioEndpointVolume)),
                   ctypes.c_int(self.CLSCTX_ALL), None, ctypes.byref(endpoint))
        if not endpoint:
            raise RuntimeError("无法获取音量接口")
        self._endpoint = endpoint
        self._original_mute = self.get_mute()

    @staticmethod
    def _call(ptr, index: int, *args):
        """按虚表索引调用 COM 方法（this 指针 + 参数）。"""
        import ctypes
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        func_addr = vtbl[index]
        prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *[type(a) for a in args])
        func = prototype(func_addr)
        return func(ptr, *args)

    # ------------------------------------------------------------------ 接口
    def get_mute(self) -> bool:
        if not self._available or not self._endpoint:
            return False
        import ctypes
        from ctypes import wintypes
        value = wintypes.BOOL()
        try:
            self._call(self._endpoint, 15, ctypes.byref(value))  # GetMute
            return bool(value.value)
        except Exception:
            return False

    def set_mute(self, mute: bool) -> bool:
        if not self._available or not self._endpoint:
            return False
        import ctypes
        from ctypes import wintypes
        try:
            self._call(self._endpoint, 14, wintypes.BOOL(int(mute)), None)  # SetMute
            return self.get_mute() == bool(mute)
        except Exception as exc:  # noqa: BLE001
            if self.logger:
                self.logger.debug(f"设置静音失败：{exc}")
            return False

    def mute(self) -> bool:
        """静音并记住原始状态。"""
        if not self._available:
            return False
        if self._original_mute is None:
            self._original_mute = self.get_mute()
        if self._original_mute:
            return True
        return self.set_mute(True)

    def restore(self) -> None:
        if not self._available or self._original_mute is None:
            return
        try:
            self.set_mute(self._original_mute)
        except Exception:
            pass
