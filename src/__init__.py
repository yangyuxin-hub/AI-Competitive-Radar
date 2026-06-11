"""竞品分析 Agent 协作系统 — 设计 v2.2.1"""
from pathlib import Path

from .encoding import configure_utf8_stdio

configure_utf8_stdio()

# .env 在包导入时即加载:任何入口只要 import src.* 就拿到凭据,不依赖入口自己记得加载。
# 此前只在 graph.py / api 入口加载 —— scripts/trace_run.py 先 import intake 时无 key,
# 静默降级启发式,竞品候选混入 "For Every"/"Dev Workflow" 解析垃圾(design-v3-draft §五实测踩坑)。
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

__version__ = "0.1.0"
