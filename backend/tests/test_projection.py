"""字段投影预演与版本隔离的回归测试（标准库 unittest，无需额外依赖）。

运行：cd backend && .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from app.services.projection import ProjectionService, ProjectionError
from app.services.projection_engine import (
    build_conflicts,
    cleanse_value,
    convert_value,
    read_path,
)


class ConversionTests(unittest.TestCase):
    def test_boolean_to_integer_is_rejected(self) -> None:
        ok, _, reason = self._convert(True, "整数")
        self.assertFalse(ok)
        self.assertIn("布尔值", reason)

    def test_mixed_type_decimal_accepts_currency(self) -> None:
        result = convert_value("￥59.90元", "小数")
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 59.9)

    def test_garbage_integer_returns_none_not_empty_string(self) -> None:
        result = convert_value("abc", "整数")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["value"])

    def test_eight_digit_date(self) -> None:
        self.assertEqual(convert_value(20240102, "日期")["value"], "2024-01-02")

    def test_out_of_range_date_explained(self) -> None:
        result = convert_value("2024-13-40", "日期")
        self.assertFalse(result["ok"])
        self.assertIn("month must be in", result["reason"])

    def test_array_text(self) -> None:
        result = convert_value(["cs", None, "systems"], "数组文本")
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "cs,systems")

    def _convert(self, value, conversion):  # type: ignore[no-untyped-def]
        result = convert_value(value, conversion)
        return result["ok"], result["value"], result["reason"]


class CleanseTests(unittest.TestCase):
    def test_tags_drop_empty_items(self) -> None:
        result = cleanse_value(["cs", None, "systems"], "标签去空项")
        self.assertEqual(result["value"], "cs,systems")
        self.assertEqual(result["status"], "applied")

    def test_unknown_operation_fails_loudly(self) -> None:
        result = cleanse_value("x", "不存在的清洗")
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["value"])


class PathTests(unittest.TestCase):
    def test_nested_read(self) -> None:
        payload = {"book": {"author": {"name": "A"}}}
        result = read_path(payload, "book.author.name")
        self.assertTrue(result["found"])
        self.assertEqual(result["value"], "A")

    def test_missing_key_explains_where(self) -> None:
        result = read_path({"book": {}}, "book.author.name")
        self.assertFalse(result["found"])
        self.assertIn("book", result["reason"])


class DraftFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ProjectionService()

    def test_active_version_is_read_only(self) -> None:
        with self.assertRaises(ProjectionError) as ctx:
            self.service.add_mapping("PV-1001", {"source": "book.title", "target": "标题"})
        self.assertIn("只读", str(ctx.exception))

    def test_active_version_preview_does_not_arm_publish(self) -> None:
        preview = self.service.preview("PV-1001")
        self.assertTrue(preview["read_only"])
        self.assertFalse(preview["publishable"])
        self.assertIsNone(self.service.get_version("PV-1001")["preview"])

    def test_undeclared_conflicts_block_preview(self) -> None:
        preview = self.service.preview("PV-1002")
        self.assertFalse(preview["publishable"])
        kinds = {conflict["kind"] for conflict in preview["conflicts"]}
        self.assertEqual(kinds, {"multi_source_target", "target_vs_cleanse"})
        for conflict in preview["conflicts"]:
            self.assertFalse(conflict["resolved"])
            self.assertIn("尚未裁决", conflict["resolution_problem"])
        # 未裁决目标不写入任何候选值，不按顺序覆盖
        record = preview["records"][0]["values"]
        self.assertNotIn("价格", record)
        self.assertNotIn("标签", record)

    def test_conflict_requires_winner_among_participants(self) -> None:
        with self.assertRaises(ProjectionError):
            self.service.resolve_conflict("PV-1002", "CF-价格", {"winner_id": "M-D13", "loser_action": "drop"})

    def test_rename_cannot_collide_with_existing_target(self) -> None:
        with self.assertRaises(ProjectionError):
            self.service.resolve_conflict(
                "PV-1002", "CF-价格",
                {"winner_id": "M-D14", "loser_action": "rename", "rename_to": "书名"},
            )

    def test_drop_requires_no_rename_target(self) -> None:
        with self.assertRaises(ProjectionError):
            self.service.resolve_conflict(
                "PV-1002", "CF-价格",
                {"winner_id": "M-D14", "loser_action": "drop", "rename_to": "零售价"},
            )

    def test_resolution_then_preview_still_blocked_by_validation(self) -> None:
        self.service.resolve_conflict("PV-1002", "CF-价格", {"winner_id": "M-D14", "loser_action": "drop"})
        self.service.resolve_conflict("PV-1002", "CF-标签", {"winner_id": "C-D1", "loser_action": "drop"})
        preview = self.service.preview("PV-1002")
        labels = [issue["label"] for issue in preview["blocking_issues"]]
        self.assertIn("必填校验「书名」", labels)
        self.assertFalse(preview["publishable"])
        # 唯一性校验只告警，不阻断
        self.assertTrue(any("唯一" in warning["label"] for warning in preview["warnings"]))
        with self.assertRaises(ProjectionError):
            self.service.publish("PV-1002")

    def test_publish_requires_preview_of_current_revision(self) -> None:
        self.service.resolve_conflict("PV-1002", "CF-价格", {"winner_id": "M-D14", "loser_action": "drop"})
        self.service.resolve_conflict("PV-1002", "CF-标签", {"winner_id": "C-D1", "loser_action": "drop"})
        self.service.update_mapping("PV-1002", "M-D11", {"default": "无题"})
        self.service.preview("PV-1002")
        # 改了内容但没重新预演 -> 旧预演失效
        self.service.add_mapping("PV-1002", {"source": "book.title", "target": "副标题"})
        with self.assertRaises(ProjectionError) as ctx:
            self.service.publish("PV-1002")
        self.assertIn("还没有预演记录", str(ctx.exception))

    def test_publish_marks_warning_gate(self) -> None:
        self.service.resolve_conflict("PV-1002", "CF-价格", {"winner_id": "M-D14", "loser_action": "drop"})
        self.service.resolve_conflict("PV-1002", "CF-标签", {"winner_id": "C-D1", "loser_action": "drop"})
        self.service.update_mapping("PV-1002", "M-D11", {"default": "无题"})
        self.service.set_validators("PV-1002", [
            {"id": "V-D1", "target": "书名", "type": "必填", "blocking": False},
            {"id": "V-D2", "target": "ISBN", "type": "必填", "blocking": False},
            {"id": "V-D3", "target": "ISBN", "type": "唯一", "blocking": False},
        ])
        self.assertTrue(self.service.preview("PV-1002")["publishable"])
        with self.assertRaises(ProjectionError) as ctx:
            self.service.publish("PV-1002")
        self.assertIn("confirm_warnings", str(ctx.exception))
        result = self.service.publish("PV-1002", force_on_warning=True)
        self.assertEqual(result["bindings"][0]["version_id"], "PV-1001")
        self.assertEqual(result["version"]["status"], "已启用")
        archived = self.service.get_version("PV-1001")
        self.assertEqual(archived["status"], "已停用")

    def test_publish_does_not_rebind_existing_tasks(self) -> None:
        self.service.resolve_conflict("PV-1002", "CF-价格", {"winner_id": "M-D14", "loser_action": "drop"})
        self.service.resolve_conflict("PV-1002", "CF-标签", {"winner_id": "C-D1", "loser_action": "drop"})
        self.service.update_mapping("PV-1002", "M-D11", {"default": "无题"})
        self.service.set_validators("PV-1002", [
            {"id": "V-D1", "target": "书名", "type": "必填", "blocking": False},
            {"id": "V-D2", "target": "ISBN", "type": "必填", "blocking": False},
            {"id": "V-D3", "target": "ISBN", "type": "唯一", "blocking": False},
        ])
        self.service.preview("PV-1002")
        self.service.publish("PV-1002", force_on_warning=True)
        binding = self.service.bindings()[0]
        self.assertEqual(binding["version_id"], "PV-1001")
        self.assertEqual(binding["version"], 1)
        # 已停用版本不能作为改绑目标
        with self.assertRaises(ProjectionError):
            self.service.rebind("SPID-0003", "PV-1001")
        rebound = self.service.rebind("SPID-0003", "PV-1002")["binding"]
        self.assertEqual(rebound["version_id"], "PV-1002")

    def test_fork_from_active_is_isolated_copy(self) -> None:
        self.service.discard_draft("PV-1002")
        draft = self.service.fork_draft("PV-1001")
        self.assertEqual(draft["status"], "编辑中")
        self.assertNotEqual(draft["id"], "PV-1001")
        self.service.add_mapping(draft["id"], {"source": "meta.views", "target": "浏览量"})
        active = self.service.get_version("PV-1001")
        self.assertEqual(len(active["mappings"]), 9)
        # 同一规则只允许一个编辑中版本
        with self.assertRaises(ProjectionError):
            self.service.fork_draft("PV-1001")
        self.service.discard_draft(draft["id"])
        self.assertIsNone(
            next((v for v in self.service.list_versions() if v["id"] == draft["id"]), None)
        )

    def test_rejected_record_is_explained_and_not_defaulted(self) -> None:
        preview = self.service.preview("PV-1002")
        rejected = [record for record in preview["records"] if not record["accepted"]]
        self.assertEqual([record["record_id"] for record in rejected], ["R-004"])
        self.assertNotIn("ISBN", rejected[0]["values"])
        self.assertIn("不使用空字符串", "；".join(rejected[0]["reject_reasons"]))

    def test_empty_cells_use_none_not_empty_string(self) -> None:
        preview = self.service.preview("PV-1002")
        issues = [issue for issue in preview["field_issues"] if issue["status"] == "empty"]
        self.assertTrue(issues)
        for issue in issues:
            self.assertIsNone(issue["value"])
        # 转换失败的值也不会写成 ""
        for record in preview["records"]:
            self.assertNotIn("", record["values"].values())

    def test_disabled_mapping_is_reported_not_silently_skipped(self) -> None:
        self.service.update_mapping("PV-1002", "M-D20", {"enabled": False})
        preview = self.service.preview("PV-1002")
        self.assertEqual([item["id"] for item in preview["disabled_mappings"]], ["M-D20"])
        self.assertNotIn("浏览量", preview["records"][0]["values"])

    def test_conflict_disappears_when_target_changes(self) -> None:
        self.service.update_mapping("PV-1002", "M-D15", {"target": "促销价"})
        conflicts = build_conflicts(self.service.get_version("PV-1002"))
        self.assertEqual([conflict["id"] for conflict in conflicts], ["CF-标签"])

    def test_stale_resolution_is_flagged_after_target_change(self) -> None:
        self.service.resolve_conflict("PV-1002", "CF-价格", {"winner_id": "M-D14", "loser_action": "drop"})
        self.service.update_mapping("PV-1002", "M-D14", {"target": "标价"})
        preview = self.service.preview("PV-1002")
        # 旧裁决不会匹配到新目标，也不会被悄悄复用到别的冲突
        self.assertEqual([conflict["id"] for conflict in preview["conflicts"]], ["CF-标签"])
        version = self.service.get_version("PV-1002")
        self.assertEqual(version["resolutions"], [])
        self.assertEqual(version["superseded_resolutions"][0]["conflict_id"], "CF-价格")
        self.assertIn("冲突已不存在", version["superseded_resolutions"][0]["reason"])
        conflicts = self.service.conflicts("PV-1002")
        self.assertEqual([item["id"] for item in conflicts["items"]], ["CF-标签"])
        self.assertEqual(len(conflicts["superseded"]), 1)
        with self.assertRaises(ProjectionError):
            self.service.resolve_conflict("PV-1002", "CF-价格", {"winner_id": "M-D14", "loser_action": "drop"})


if __name__ == "__main__":
    unittest.main()
