"""投影预演示例数据：嵌套对象、空值、混合类型样本，以及现用版本与编辑中版本。

设计要点：
- 现用版本 PV-1001 是既有任务已经绑定的口径，内容保持稳定、没有冲突。
- 编辑中版本 PV-1002 故意留下两类冲突（同一目标多源写入、目标名与清洗输出重名），
  必须先明确裁决才能通过预演。
- 样本里混入空值、嵌套数组、布尔值冒充整数、带货币符号的小数、重复 ISBN，
  用来验证类型转换、默认填充、清洗、必填与唯一性校验的分步行为。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

SAMPLE_DATASET: dict[str, Any] = {
    "id": "SAMPLE-BOOK-01",
    "name": "图书嵌套样本",
    "description": (
        "含嵌套对象、空值与混合类型。R-001 与 R-002 的 ISBN 清洗后相同，用于唯一性校验；"
        "R-002 的书名只有空白、库存是 abc、出版日期越界；R-003 的库存是布尔值、"
        "出版日期是整数、价格带“元”；R-004 缺少必填 ISBN，演示拒收且不用空字符串补齐。"
    ),
    "records": [
        {
            "record_id": "R-001",
            "label": "完整记录",
            "payload": {
                "book": {
                    "title": "  深入理解计算机系统  ",
                    "author": {"name": "Randal E. Bryant", "tags": ["cs", None, "systems"]},
                    "price": "￥59.90",
                    "price_alt": 59.9,
                    "isbn": "978-7-111-54493-7",
                    "stock": "12",
                    "published_at": "2024/01/02",
                    "rating": 4.5,
                },
                "meta": {"source": None, "views": "1,234"},
            },
        },
        {
            "record_id": "R-002",
            "label": "空值与重复 ISBN",
            "payload": {
                "book": {
                    "title": "   ",
                    "author": {"name": None, "tags": []},
                    "price": None,
                    "price_alt": "59.9",
                    "isbn": "9787111544937",
                    "stock": "abc",
                    "published_at": "2024-13-40",
                    "rating": None,
                },
                "meta": None,
            },
        },
        {
            "record_id": "R-003",
            "label": "混合类型",
            "payload": {
                "book": {
                    "title": "计算机网络：自顶向下方法",
                    "author": {"name": "James F. Kurose", "tags": ["networking", 42, None]},
                    "price": 89,
                    "price_alt": "89.00元",
                    "isbn": "978-7-111-49783-4",
                    "stock": True,
                    "published_at": 20240102,
                    "rating": "4.8",
                },
                "meta": {"source": "crawler", "views": "998"},
            },
        },
        {
            "record_id": "R-004",
            "label": "缺少必填 ISBN",
            "payload": {
                "book": {
                    "title": "算法导论",
                    "author": {"name": "Thomas H. Cormen", "tags": ["algorithms"]},
                    "price": "128.00",
                    "price_alt": "128.00元",
                    "isbn": None,
                    "stock": "7",
                    "published_at": "2023-06-01",
                    "rating": "4.9",
                },
                "meta": {"source": "crawler", "views": "456"},
            },
        },
    ],
}

# 现用版本：既有任务绑定的口径，无冲突。
ACTIVE_MAPPINGS: list[dict[str, Any]] = [
    {"id": "M-A1", "source": "book.title", "target": "书名", "convert": "文本",
     "default": "", "cleanse": "去首尾空白", "required": True, "enabled": True},
    {"id": "M-A2", "source": "book.author.name", "target": "作者", "convert": "文本",
     "default": "佚名", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-A3", "source": "book.author.tags", "target": "标签", "convert": "数组文本",
     "default": "", "cleanse": "标签去空项", "required": False, "enabled": True},
    {"id": "M-A4", "source": "book.price_alt", "target": "价格", "convert": "小数",
     "default": "", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-A5", "source": "book.isbn", "target": "ISBN", "convert": "文本",
     "default": "", "cleanse": "去除分隔符", "required": True, "enabled": True},
    {"id": "M-A6", "source": "book.published_at", "target": "出版日期", "convert": "日期",
     "default": "", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-A7", "source": "book.stock", "target": "库存", "convert": "整数",
     "default": "0", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-A8", "source": "meta.views", "target": "浏览量", "convert": "整数",
     "default": "0", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-A9", "source": "meta.source", "target": "来源", "convert": "文本",
     "default": "未知", "cleanse": "无", "required": False, "enabled": True},
]

ACTIVE_VALIDATORS: list[dict[str, Any]] = [
    {"id": "V-A1", "target": "书名", "type": "必填", "blocking": True},
    {"id": "V-A2", "target": "ISBN", "type": "必填", "blocking": True},
    {"id": "V-A3", "target": "ISBN", "type": "唯一", "blocking": False},
]

# 编辑中版本：留下两处冲突，等待人工裁决。
DRAFT_MAPPINGS: list[dict[str, Any]] = [
    {"id": "M-D11", "source": "book.title", "target": "书名", "convert": "文本",
     "default": "", "cleanse": "去首尾空白", "required": False, "enabled": True},
    {"id": "M-D12", "source": "book.author.name", "target": "作者", "convert": "文本",
     "default": "佚名", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-D13", "source": "book.author.tags", "target": "标签", "convert": "数组文本",
     "default": "", "cleanse": "标签去空项", "required": False, "enabled": True},
    {"id": "M-D14", "source": "book.price", "target": "价格", "convert": "小数",
     "default": "", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-D15", "source": "book.price_alt", "target": "价格", "convert": "小数",
     "default": "", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-D16", "source": "book.isbn", "target": "ISBN", "convert": "文本",
     "default": "", "cleanse": "去除分隔符", "required": True, "enabled": True},
    {"id": "M-D17", "source": "book.stock", "target": "库存", "convert": "整数",
     "default": "0", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-D18", "source": "book.published_at", "target": "出版日期", "convert": "日期",
     "default": "", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-D19", "source": "book.rating", "target": "评分", "convert": "小数",
     "default": "", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-D20", "source": "meta.views", "target": "浏览量", "convert": "整数",
     "default": "0", "cleanse": "无", "required": False, "enabled": True},
    {"id": "M-D21", "source": "meta.source", "target": "来源", "convert": "文本",
     "default": "未知", "cleanse": "无", "required": False, "enabled": True},
]

DRAFT_CLEANSE_RULES: list[dict[str, Any]] = [
    {"id": "C-D1", "source": "book.author.tags", "output": "标签",
     "op": "标签去空项", "enabled": True,
     "note": "独立清洗规则也写「标签」，与投影目标重名，必须裁决谁写入。"},
]

DRAFT_VALIDATORS: list[dict[str, Any]] = [
    {"id": "V-D1", "target": "书名", "type": "必填", "blocking": True},
    {"id": "V-D2", "target": "ISBN", "type": "必填", "blocking": True},
    {"id": "V-D3", "target": "ISBN", "type": "唯一", "blocking": False},
]


def _version(
    *,
    version_id: str,
    rule_id: int,
    rule_code: str,
    version: int,
    status: str,
    mappings: list[dict[str, Any]],
    cleanse_rules: list[dict[str, Any]] | None = None,
    validators: list[dict[str, Any]],
    resolutions: list[dict[str, Any]] | None = None,
    published_at: str | None,
    note: str,
) -> dict[str, Any]:
    return {
        "id": version_id,
        "rule_id": rule_id,
        "rule_code": rule_code,
        "name": "图书字段投影",
        "version": version,
        "status": status,
        "created_at": "2026-09-03 09:30",
        "updated_at": "2026-09-03 10:00",
        "updated_by": "值班管理员",
        "published_at": published_at,
        "sample_id": SAMPLE_DATASET["id"],
        "note": note,
        "mappings": deepcopy(mappings),
        "cleanse_rules": deepcopy(cleanse_rules or []),
        "validators": deepcopy(validators),
        "resolutions": deepcopy(resolutions or []),
        "preview": None,
    }


PROJECTION_VERSIONS: list[dict[str, Any]] = [
    _version(
        version_id="PV-1001",
        rule_id=3,
        rule_code="EXTR-0003",
        version=1,
        status="已启用",
        mappings=ACTIVE_MAPPINGS,
        validators=ACTIVE_VALIDATORS,
        published_at="2026-09-03 10:00",
        note="现用版本；既有采集任务继续按此版本口径执行，不随草稿或发布自动切换。",
    ),
    _version(
        version_id="PV-1002",
        rule_id=3,
        rule_code="EXTR-0003",
        version=2,
        status="编辑中",
        mappings=DRAFT_MAPPINGS,
        cleanse_rules=DRAFT_CLEANSE_RULES,
        validators=DRAFT_VALIDATORS,
        published_at=None,
        note="编辑中草稿：含未裁决冲突，不能替换现用版本。",
    ),
]

# 任务与投影版本的绑定：发布新版本不会改写这些绑定，保留原口径。
PROJECTION_BINDINGS: list[dict[str, Any]] = [
    {
        "task_code": "SPID-0003",
        "task_name": "采集任务样例3",
        "rule_id": 3,
        "rule_code": "EXTR-0003",
        "version_id": "PV-1001",
        "version": 1,
        "bound_at": "2026-09-03 10:05",
        "semantics": "按绑定版本 PV-1001 执行；新版本发布后仍需显式改绑才会生效。",
    }
]
