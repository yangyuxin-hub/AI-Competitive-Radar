"""Doubao / ARK LLM 客户端封装。

环境变量:
- ARK_API_KEY: 必填(除非 ANALYZER_MOCK=1)
- ARK_EP: 模型 endpoint id,默认 doubao-seed-2.0-lite 占位
- ANALYZER_MOCK=1: 跳过真实 LLM 调用,直接返回 data/sample_report.json 的内容,
  用于无 API key 的环境跑通端到端骨架
"""
from __future__ import annotations

import json
import os
import re
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


def _trace_enabled() -> bool:
    # 默认开启;LLM_TRACE=0 关闭
    return os.environ.get("LLM_TRACE", "1").strip() not in ("0", "false", "False")


def _trace_call(label: str, model: str, system_prompt: str, user_payload: dict,
                raw: str, prompt_tokens: int, completion_tokens: int, elapsed: float) -> None:
    """把每次 LLM 调用的完整输入/输出/token/耗时追加到 logs/llm_calls.jsonl,供离线分析优化。"""
    if not _trace_enabled():
        return
    try:
        _LOGS_DIR.mkdir(exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(),
            "label": label,
            "model": model,
            "duration_sec": round(elapsed, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "system_prompt": system_prompt,
            "user_payload": user_payload,
            "raw_output": raw,
        }
        with _TRACE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"[llm] trace 写入失败(忽略): {e}")


# ───────────────────────────────────────────────────────────────────────
# 进度回调(UI 实时刷新用)
# ───────────────────────────────────────────────────────────────────────

_LLM_CALLBACK = None  # type: Optional[callable]


def set_llm_callback(cb) -> None:
    """注册 LLM 调用事件回调。事件字典字段:
    {label, phase: 'start'|'done', duration, prompt_tokens, completion_tokens, json_mode}
    回调失败不影响主流程。"""
    global _LLM_CALLBACK
    _LLM_CALLBACK = cb


def _emit_llm(**evt) -> None:
    if _LLM_CALLBACK is None:
        return
    try:
        _LLM_CALLBACK(evt)
    except Exception:
        pass


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

        # 默认走小米 MiMo(TTFT ~2-5s,远快于 Doubao EP 的 25-38s);ARK_* 仍可回退兼容
        api_key = os.environ.get("LLM_API_KEY") or os.environ.get("ARK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "LLM_API_KEY / ARK_API_KEY 未设置。若要无 API key 跑骨架,设置 ANALYZER_MOCK=1"
            )
        base_url = (
            os.environ.get("LLM_BASE_URL")
            or os.environ.get("ARK_BASE_URL")
            or "https://token-plan-cn.xiaomimimo.com/v1"
        )
        timeout = float(os.environ.get("LLM_TIMEOUT", os.environ.get("ARK_TIMEOUT", "120")))
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
        max_tokens: int = 4096,
        label: str = "call",
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
        model = model or os.environ.get("LLM_MODEL") or os.environ.get("ARK_EP") or "mimo-v2.5-pro"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

        t0 = time.time()
        _emit_llm(label=label, phase="start")
        # 本 EP(豆包)不支持 response_format=json_object,直接走文本模式(省掉一轮被拒的往返)。
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        elapsed = time.time() - t0
        raw = resp.choices[0].message.content or "{}"
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        print(f"[llm] {label}: {elapsed:.1f}s · prompt={prompt_tokens} completion={completion_tokens}")
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


# 单例
_default_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
