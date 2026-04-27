"""Compatibility shim for the relocated webview launcher module."""

import sys

from web import webview_launcher as _webview_launcher

if __name__ == "__main__":
    _webview_launcher.main()
else:
    sys.modules[__name__] = _webview_launcher

