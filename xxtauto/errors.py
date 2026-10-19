# -*- coding: utf-8 -*-
"""自定义异常。"""


class StopRequested(Exception):
    """用户按下 Esc 请求停止。"""


class PageLost(Exception):
    """浏览器窗口/页面丢失。"""
