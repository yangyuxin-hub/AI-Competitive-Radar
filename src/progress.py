"""共享进度回调通道 + 跨线程上下文传播执行器 — 供 collector / analyzer 等节点复用。

每个节点持有**独立**的 ProgressChannel 实例(api 对各节点分别注册不同回调,
不能共用一个全局通道,否则 SSE 事件会串台)。各模块仍以原公开名
`set_progress_callback` / `_emit_progress` 暴露薄封装,callsite 与 api 接线零变化。

回调用 `contextvars.ContextVar` 存,而非进程级全局:每个 `/api/run` 请求在自己的
worker 线程里跑完整条流水线,context 天然按请求隔离——多请求并发(或中断重开留下的
僵尸 worker 线程)时,一个 run 注册的回调绝不会被另一个 run 的 emit 命中(进程级全局
会串台:旧 run 的僵尸线程 emit 会打到新 run 的回调上,导致时间线/token 跨 run 污染)。

**但** ContextVar 默认不跨 `ThreadPoolExecutor` 子线程继承(子线程起始 context 为空)。
collector/analyzer 用线程池并发抓取/分析,子线程里的 `_emit_progress` 会丢事件。
解法:所有线程池改用本模块的 `CtxThreadPoolExecutor`——它在 `submit` 时(父线程)
快照当前 context 并让 worker 在该快照里运行,于是回调/llm 归因/token 累加器都能跨线程
传播,同时保持 run 间隔离。`Executor.map` 内部走 `submit`,故 map 亦自动覆盖。

回调失败一律静默 —— 进度是旁路观测,绝不影响主流程。
"""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional


class ProgressChannel:
    """单个节点的进度事件通道(回调按 run 的 context 隔离)。"""

    def __init__(self) -> None:
        self._cb: contextvars.ContextVar[Optional[Callable[[dict], None]]] = (
            contextvars.ContextVar("progress_cb", default=None)
        )

    def set_callback(self, cb: Optional[Callable[[dict], None]]) -> None:
        """注册/清空回调。传 None 即解绑。"""
        self._cb.set(cb)

    def emit(self, **event) -> None:
        """发事件(防御性拷贝)。无回调或回调抛错都静默。"""
        cb = self._cb.get()
        if cb is None:
            return
        try:
            cb(dict(event))
        except Exception:  # noqa: BLE001 — 进度旁路,失败不影响主流程
            pass


class CtxThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor,但每个 task 在提交时父线程的 contextvars 快照里运行。

    用途:让 ContextVar 存的 progress 回调 / llm 回调 / run-stage 归因 / token 累加器
    跨子线程传播(默认子线程拿不到父线程的 ContextVar 值),同时保持按 run 隔离。
    `submit` 覆盖即可——`Executor.map` 内部调 `submit`,故 map 一并覆盖。
    """

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)
