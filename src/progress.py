"""共享进度回调通道 — 供 collector / analyzer 等节点复用。

每个节点持有**独立**的 ProgressChannel 实例(api 对各节点分别注册不同回调,
不能共用一个全局通道,否则 SSE 事件会串台)。各模块仍以原公开名
`set_progress_callback` / `_emit_progress` 暴露薄封装,callsite 与 api 接线零变化。

回调失败一律静默 —— 进度是旁路观测,绝不影响主流程。
"""
from __future__ import annotations

from typing import Callable, Optional


class ProgressChannel:
    """单个节点的进度事件通道。"""

    def __init__(self) -> None:
        self._cb: Optional[Callable[[dict], None]] = None

    def set_callback(self, cb: Optional[Callable[[dict], None]]) -> None:
        """注册/清空回调。传 None 即解绑。"""
        self._cb = cb

    def emit(self, **event) -> None:
        """发事件(防御性拷贝)。无回调或回调抛错都静默。"""
        cb = self._cb
        if cb is None:
            return
        try:
            cb(dict(event))
        except Exception:  # noqa: BLE001 — 进度旁路,失败不影响主流程
            pass
