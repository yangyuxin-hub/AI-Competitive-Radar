"""Doubao / ARK LLM 客户端封装。

环境变量:
- ARK_API_KEY: 必填(除非 ANALYZER_MOCK=1)
- ARK_EP: 模型 endpoint id,默认 doubao-seed-2.0-lite 占位
- ANALYZER_MOCK=1: 跳过真实 LLM 调用,直接返回 data/sample_report.json 的内容,
  用于无 API key 的环境跑通端到端骨架
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


_ROOT = Path(__file__).resolve().parent.parent
_SAMPLE_REPORT_PATH = _ROOT / "data" / "sample_report.json"
_LOGS_DIR = _ROOT / "logs"

# 匹配 ```json ... ``` 或 ``` ... ``` 围栏
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _strip_json(text: str) -> str:
    """从 LLM 输出中提取 JSON 文本:支持 ```json 围栏、纯 JSON、混杂前后缀文字"""
    text = text.strip()
    if not text:
        return "{}"
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # 取首个 { 到末个 } 之间内容
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _save_raw(label: str, content: str) -> Path:
    _LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = _LOGS_DIR / f"llm_raw_{label}_{ts}.txt"
    path.write_text(content, encoding="utf-8")
    return path


_TRACE_PATH = _LOGS_DIR / "llm_calls.jsonl"
_PROMPTS_DIR = _LOGS_DIR / "llm_prompts"   # system_prompt 内容寻址去重存放处
_TRACE_LOCK = threading.Lock()             # 并行 section 同时写 jsonl 时防交错/损坏行
_TRACE_MAX_BYTES = 20 * 1024 * 1024        # 单文件 20MB 即轮转,防无限膨胀


def _trace_enabled() -> bool:
    # 默认开启;LLM_TRACE=0 关闭
    return os.environ.get("LLM_TRACE", "1").strip() not in ("0", "false", "False")


def _save_prompt_once(system_prompt: str) -> str:
    """system_prompt 内容寻址:同一 prompt 只落一份到 llm_prompts/<hash>.txt,
    jsonl 里只存 hash。深跑同一 facts/derivations prompt 重复 50+ 次,去重后
    llm_calls.jsonl 体积砍掉绝大部分(prompt 占每条记录的大头)。"""
    digest = hashlib.sha1(system_prompt.encode("utf-8")).hexdigest()[:12]
    try:
        _PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _PROMPTS_DIR / f"{digest}.txt"
        if not path.exists():
            path.write_text(system_prompt, encoding="utf-8")
    except OSError:
        pass
    return digest


def _rotate_trace_if_needed() -> None:
    """超过阈值就把当前 jsonl 滚成 .1 备份(只留一代,够排查近一次运行)。"""
    try:
        if _TRACE_PATH.exists() and _TRACE_PATH.stat().st_size > _TRACE_MAX_BYTES:
            _TRACE_PATH.replace(_TRACE_PATH.with_suffix(".jsonl.1"))
    except OSError:
        pass


def _trace_call(label: str, model: str, system_prompt: str, user_payload: dict,
                raw: str, prompt_tokens: int, completion_tokens: int, elapsed: float) -> None:
    """把每次 LLM 调用的输入/输出/token/耗时追加到 logs/llm_calls.jsonl,供离线分析优化。
    system_prompt 去重存 llm_prompts/,这里只记 hash;按 run_id/stage 归因便于逐环节分析。"""
    if not _trace_enabled():
        return
    try:
        rs = _RUN_STAGE.get()
        rec = {
            "ts": datetime.now().isoformat(),
            "run_id": rs[0] if rs else None,
            "stage": rs[1] if rs else None,
            "label": label,
            "model": model,
            "duration_sec": round(elapsed, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "system_prompt_hash": _save_prompt_once(system_prompt),
            "user_payload": user_payload,
            "raw_output": raw,
        }
        with _TRACE_LOCK:
            _LOGS_DIR.mkdir(exist_ok=True)
            _rotate_trace_if_needed()
            with _TRACE_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[llm] trace 写入失败(忽略): {e}")


# ───────────────────────────────────────────────────────────────────────
# 进度回调(UI 实时刷新用)+ 按 run/stage 的成本归因
# ───────────────────────────────────────────────────────────────────────

# 回调用 contextvar:每个 /api/run 请求在自己 worker 线程里跑,天然按请求隔离,
# 不会像进程级全局那样被并发请求互相覆盖(与 progress.ProgressChannel 同理)。
_LLM_CALLBACK: contextvars.ContextVar[Optional[object]] = contextvars.ContextVar(
    "llm_callback", default=None)
# 当前 graph 节点的 (run_id, stage),由 graph._instrument 在进节点时设置,_trace_call 写入。
_RUN_STAGE: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar(
    "llm_run_stage", default=None)
# 本节点 LLM token 累加器,graph._instrument 进节点时 begin、出节点时读 → StageReport.cost。
_TOKEN_ACC: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "llm_token_acc", default=None)


def set_llm_callback(cb) -> None:
    """注册 LLM 调用事件回调。事件字典字段:
    {label, phase: 'start'|'done', duration, prompt_tokens, completion_tokens, json_mode}
    回调失败不影响主流程。"""
    _LLM_CALLBACK.set(cb)


def _emit_llm(**evt) -> None:
    cb = _LLM_CALLBACK.get()
    if cb is None:
        return
    try:
        cb(evt)
    except Exception:
        pass


def set_run_stage(run_id: Optional[str], stage: Optional[str]) -> None:
    """graph._instrument 进节点时调用:后续该节点内所有 LLM 调用都按此 run/stage 归因。"""
    _RUN_STAGE.set((run_id, stage))


def begin_token_accumulator() -> dict:
    """graph._instrument 进节点时调用:开一个本节点的 token 累加器并返回(出节点时读)。"""
    acc = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    _TOKEN_ACC.set(acc)
    return acc


def _accumulate_tokens(prompt_tokens: int, completion_tokens: int) -> None:
    acc = _TOKEN_ACC.get()
    if acc is not None:
        acc["prompt_tokens"] += prompt_tokens or 0
        acc["completion_tokens"] += completion_tokens or 0
        acc["calls"] += 1


def is_mock_mode() -> bool:
    return os.environ.get("ANALYZER_MOCK", "").strip() in ("1", "true", "True")


def load_sample_report() -> dict:
    """Mock 模式下,把 sample_report.json 拆成 facts / derivations 两部分"""
    with _SAMPLE_REPORT_PATH.open(encoding="utf-8") as f:
        return json.load(f)


class LLMClient:
    """Lazy-init OpenAI client targeting ARK."""

    def __init__(self) -> None:
        self._client = None

    def _ensure(self):
        if self._client is not None:
            return self._client
        if is_mock_mode():
            return None
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("openai SDK 未安装。pip install -r requirements.txt") from e

        api_key = os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "LLM_API_KEY / ARK_API_KEY 未设置。若要无 API key 跑骨架,设置 ANALYZER_MOCK=1"
            )
        # 显式 LLM_* 优先;否则只要使用 ARK_API_KEY/ARK_EP,就走火山 ARK 端点。
        # 避免把 ARK key 误发到默认 MiMo 地址,造成 401 后被上层误判为超时。
        if os.environ.get("LLM_BASE_URL") or os.environ.get("LLM_MODEL") or os.environ.get("LLM_API_KEY"):
            base_url = os.environ.get("LLM_BASE_URL") or "https://token-plan-cn.xiaomimimo.com/v1"
        else:
            base_url = os.environ.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"
        timeout = float(os.environ.get("LLM_TIMEOUT", os.environ.get("ARK_TIMEOUT", "200")))
        import httpx  # 关掉系统代理(实测代理给 LLM 调用平添 ~10s)
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=int(os.environ.get("LLM_MAX_RETRIES", os.environ.get("ARK_MAX_RETRIES", "1"))),
            http_client=httpx.Client(trust_env=False, timeout=timeout),
        )
        return self._client

    def call_json(
        self,
        system_prompt: str,
        user_payload: dict,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        label: str = "call",
        timeout: Optional[float] = None,
    ) -> dict:
        """调用 LLM,要求返回 JSON 对象。Mock 模式下不实际调用。

        策略:
        1. 先尝试 response_format=json_object(豆包大部分模型支持)
        2. 若服务端拒绝,降级为普通文本调用 + _strip_json 抽取
        3. JSON 解析失败时,把原始输出落盘到 logs/ 便于排查
        """
        if is_mock_mode():
            raise RuntimeError(
                "Mock 模式下不应直接调用 call_json,Analyzer 应该走 load_sample_report"
            )

        client = self._ensure()
        if model is None:
            if os.environ.get("LLM_MODEL") or os.environ.get("LLM_API_KEY"):
                model = os.environ.get("LLM_MODEL") or "mimo-v2.5-pro"
            else:
                model = os.environ.get("ARK_EP") or "doubao-seed-2-0-lite-250428"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

        t0 = time.time()
        _emit_llm(label=label, phase="start")
        # 本 EP(豆包)不支持 response_format=json_object,直接走文本模式(省掉一轮被拒的往返)。
        # max_tokens=None → 不传上限,让模型按内容自然结束(报告生成切忌中途截断 → 残缺 JSON)。
        kwargs = {"model": model, "messages": messages, "temperature": 0.2}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        # 单次调用可覆写超时(facts 并行子调用用更短的超时 → hung 的 section 快速降级,
        # 不必等共享 client 的 200s)。其余字段沿用 _ensure() 建好的 client。
        call_client = client.with_options(timeout=timeout) if timeout is not None else client
        resp = call_client.chat.completions.create(**kwargs)
        elapsed = time.time() - t0
        raw = resp.choices[0].message.content or "{}"
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        print(f"[llm] {label}: {elapsed:.1f}s · prompt={prompt_tokens} completion={completion_tokens}")
        _accumulate_tokens(prompt_tokens, completion_tokens)
        _emit_llm(
            label=label, phase="done",
            duration=elapsed, json_mode=False,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        )
        _trace_call(label, model, system_prompt, user_payload, raw,
                    prompt_tokens, completion_tokens, elapsed)

        # 解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            cleaned = _strip_json(raw)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as e:
                path = _save_raw(label, raw)
                raise RuntimeError(
                    f"LLM 输出无法解析为 JSON: {e}; 原始输出已保存到 {path}"
                ) from e

    def stream_json(
        self,
        system_prompt: str,
        user_payload: dict,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        label: str = "call",
        timeout: Optional[float] = None,
    ):
        """流式版 call_json。生成器,逐块 yield 过程事件,最后 yield 解析结果:
            ("reasoning", delta)  模型思维链增量(模型走 reasoning_content 通道时才有)
            ("answer", delta)     正式输出(JSON 正文)字符增量
            ("done", dict)        解析后的完整 JSON;解析失败 → {}
        给「等待感重」的 intake 用:把一次性阻塞调用变成可见的逐字思考。Mock 模式不应调用。"""
        if is_mock_mode():
            raise RuntimeError("Mock 模式下不应直接调用 stream_json")

        client = self._ensure()
        if model is None:
            if os.environ.get("LLM_MODEL") or os.environ.get("LLM_API_KEY"):
                model = os.environ.get("LLM_MODEL") or "mimo-v2.5-pro"
            else:
                model = os.environ.get("ARK_EP") or "doubao-seed-2-0-lite-250428"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        kwargs = {"model": model, "messages": messages, "temperature": 0.2, "stream": True}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        call_client = client.with_options(timeout=timeout) if timeout is not None else client

        t0 = time.time()
        _emit_llm(label=label, phase="start")
        parts: list[str] = []
        try:
            stream = call_client.chat.completions.create(**kwargs)
            for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = choices[0].delta
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    yield ("reasoning", rc)
                c = getattr(delta, "content", None)
                if c:
                    parts.append(c)
                    yield ("answer", c)
        except Exception as e:  # noqa: BLE001 — 流式失败 → 给空结果,上层回退启发式
            print(f"[llm] {label}(stream) 失败: {type(e).__name__}: {e}")
            yield ("done", {})
            return

        raw = "".join(parts) or "{}"
        elapsed = time.time() - t0
        print(f"[llm] {label}(stream): {elapsed:.1f}s · chars={len(raw)}")
        _accumulate_tokens(0, 0)  # 流式无 usage,只记一次调用计数
        _emit_llm(label=label, phase="done", duration=elapsed, json_mode=False,
                  prompt_tokens=0, completion_tokens=0)
        try:
            _trace_call(label, model, system_prompt, user_payload, raw, 0, 0, elapsed)
        except Exception:  # noqa: BLE001
            pass

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            try:
                parsed = json.loads(_strip_json(raw))
            except json.JSONDecodeError:
                _save_raw(label, raw)
                parsed = {}
        yield ("done", parsed)


# 单例
_default_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
