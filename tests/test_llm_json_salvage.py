"""LLM JSON 输出的截断修复 + 验尸文件落盘。

2026-06-10/11 实测:facts:pricing_model 大输出撞服务端 completion 上限被拦腰截断
(尾部停在 `"observed_at": "2026-`),旧逻辑整段报废走骨架兜底。修复后应能闭合
未结的字符串/括号、砍掉尾部碎片,保住已生成的绝大部分数据。

另:label 含 `:`(如 facts:pricing_model)时,Windows NTFS 把冒号当备用数据流
分隔符,验尸文件内容全进隐藏流、主文件 0 字节——文件名必须消毒。
"""
import json
import unittest

from src.llm import _save_raw, repair_truncated_json


class RepairTruncatedJsonTest(unittest.TestCase):
    def test_truncated_mid_string_salvaged(self):
        # 实际验尸样本的最小复刻:截断发生在字符串值中间
        raw = ('{"products": [{"name": "Cursor", "tiers": [{"tier_name": "Pro", '
               '"price": {"amount": 20, "currency": "USD"}, "observed_at": "2026-')
        out = repair_truncated_json(raw)
        self.assertIsInstance(out, dict)
        prods = out["products"]
        self.assertEqual(prods[0]["name"], "Cursor")
        self.assertEqual(prods[0]["tiers"][0]["price"]["amount"], 20)

    def test_truncated_dangling_key_salvaged(self):
        # 截断停在「有 key 没 value」处 → 应砍掉碎片再闭合
        raw = '{"a": {"b": [1, 2, 3]}, "c": {"d": "ok", "e":'
        out = repair_truncated_json(raw)
        self.assertIsInstance(out, dict)
        self.assertEqual(out["a"]["b"], [1, 2, 3])
        self.assertEqual(out.get("c", {}).get("d"), "ok")

    def test_complete_json_passthrough(self):
        raw = '{"x": 1}'
        self.assertEqual(repair_truncated_json(raw), {"x": 1})

    def test_hopeless_input_returns_none(self):
        self.assertIsNone(repair_truncated_json("not json at all"))


class SaveRawSanitizeTest(unittest.TestCase):
    def test_label_colon_sanitized(self):
        path = _save_raw("facts:pricing_model", '{"probe": 1}')
        try:
            # 文件名不得含 NTFS ADS 分隔符,内容必须落在主流(size > 0)
            self.assertNotIn(":", path.name)
            self.assertGreater(path.stat().st_size, 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["probe"], 1)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
