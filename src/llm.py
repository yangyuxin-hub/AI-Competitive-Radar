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
from pathlib import Path
from typing import Optional


_ROOT = Path(__file__).resolve().parent.parent
_SAMPLE_REPORT_PATH = _ROOT / "data" / "sample_report.json"


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

        api_key = os.environ.get("ARK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ARK_API_KEY 未设置。若要无 API key 跑骨架,设置 ANALYZER_MOCK=1"
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            timeout=float(os.environ.get("ARK_TIMEOUT", "90")),
            max_retries=2,
        )
        return self._client

    def call_json(
        self,
        system_prompt: str,
        user_payload: dict,
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> dict:
        """调用 LLM,要求返回 JSON 对象。Mock 模式下不实际调用。"""
        if is_mock_mode():
            raise RuntimeError(
                "Mock 模式下不应直接调用 call_json,Analyzer 应该走 load_sample_report"
            )

        client = self._ensure()
        model = model or os.environ.get("ARK_EP", "doubao-seed-2-0-lite")

        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        return json.loads(content)


# 单例
_default_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
