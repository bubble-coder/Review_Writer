import unittest

from review_writer.markdown_view import parse_markdown_blocks


class MarkdownViewParserTests(unittest.TestCase):
    def test_parses_research_plan_structure(self) -> None:
        blocks = parse_markdown_blocks(
            """# 调研计划

> 状态：待确认

## 核心问题
1. **疗效**如何？
- [x] 已完成检索式

| 数据库 | 查询式 |
| --- | --- |
| PubMed | `term` |

```text
raw query
```
"""
        )

        kinds = [block.kind for block in blocks]
        self.assertIn("heading", kinds)
        self.assertIn("quote", kinds)
        self.assertIn("list", kinds)
        self.assertIn("table_header", kinds)
        self.assertIn("table_row", kinds)
        self.assertIn("code", kinds)
        self.assertEqual(blocks[0].level, 1)
        self.assertEqual(blocks[0].text, "调研计划")
        self.assertTrue(any(block.text.startswith("☑") for block in blocks))

    def test_unclosed_code_fence_is_still_readable(self) -> None:
        blocks = parse_markdown_blocks("## 查询式\n```\nTITLE(foo)")

        self.assertEqual(blocks[-1].kind, "code")
        self.assertEqual(blocks[-1].text, "TITLE(foo)")


if __name__ == "__main__":
    unittest.main()
