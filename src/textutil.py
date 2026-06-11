"""纯文本叶子工具(零依赖) — 供采集/分析两侧共用。

原住 collector_common;v3 M3 拆出,切断 Analyst 簇对采集层的 import 边
(analyzer_common 只为 smart_truncate 引 collector_common,违反"Analyst 零采集依赖")。
collector_common 对本模块 re-export 保 back-compat。
"""
from __future__ import annotations

import re

# 数字/价格模式:匹配 $12, 12.5%, ¥99, €10, 12K, 1.5M, 100ms, 30s 等
_NUM_PATTERN = re.compile(r'[\$¥€£]\s*\d[\d,.]*|\d[\d,.]*\s*(?:%|ms|s|GB|MB|KB|TB|K|M|B|万|亿|元|美元|月|天|年|倍|倍率|[Cc]ents?|[Dd]ollars?)')


def smart_truncate(text: str, max_len: int) -> str:
    """A3: 智能截断 — 优先保留含数字/价格的句子，避免截断点丢关键数据。

    策略:
    1. 按句子拆分，找含数字的句子优先拼入
    2. 若含数字句子已够长，直接返回
    3. 否则用剩余空间补充上下文句子
    4. 兜底:无数字句子或拆分失败时，回退前 N 字符
    """
    if len(text) <= max_len:
        return text
    # 按中英文句号/分号/换行拆
    sentences = re.split(r'(?<=[。；！？.!?\n])\s*', text)
    if len(sentences) <= 1:
        return text[:max_len]
    # 分两桶:含数字 vs 不含
    with_num = [s for s in sentences if _NUM_PATTERN.search(s)]
    without_num = [s for s in sentences if not _NUM_PATTERN.search(s)]
    result = ""
    # 先拼含数字的句子
    for s in with_num:
        if len(result) + len(s) + 1 > max_len:
            break
        result += s + " "
    # 剩余空间补上下文
    for s in without_num:
        if len(result) + len(s) + 1 > max_len:
            break
        result += s + " "
    return result.strip() if result.strip() else text[:max_len]
