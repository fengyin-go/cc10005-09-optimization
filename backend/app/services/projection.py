"""字段投影业务服务：版本隔离、草稿编辑、冲突裁决、预演与发布闸门。

隔离规则：
- 已启用版本是只读快照，任何编辑请求都会被要求先派生编辑草稿。
- 编辑草稿带 revision，每次改动自增；预演结果绑定 revision，改动后旧预演自动失效。
- 只有「预演通过且与当前 revision 一致」的草稿才允许发布。
- 发布生成新的已启用版本并归档旧版本，但任务绑定表不随之改写，已有任务继续按原版本口径执行。
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.projection_seed import PROJECTION_BINDINGS, PROJECTION_VERSIONS, SAMPLE_DATASET
from app.services.projection_engine import (
    CLEANSE_OPERATIONS,
    CONVERSIONS,
    LOSER_ACTIONS,
    RESOLUTION_STRATEGIES,
    build_conflicts,
    build_preview,
    collect_writers,
    conflicts_with_status,
)

STATUS_DIRTY = "编辑中"
STATUS_ACTIVE = "已启用"
STATUS_ARCHIVED = "已停用"
EDITABLE_STATUS = {STATUS_DIRTY}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _public(version: dict[str, Any]) -> dict[str, Any]:
    """对外返回版本时剔除以下划线开头的内部字段（如预演样本缓存）。"""
    return deepcopy({key: value for key, value in version.items() if not key.startswith("_")})


class ProjectionError(Exception):
    """业务规则拒绝操作时抛出；路由层把它转成可读的失败结果。"""


class ProjectionService:
    def __init__(self) -> None:
        self._versions: list[dict[str, Any]] = deepcopy(PROJECTION_VERSIONS)
        # 单调递增的编号计数器：丢弃/归档后也不复用旧编号，便于审计追溯
        self._sequences = {"PV": 1000, "M": 0, "C": 0, "V": 0}
        self._bindings: list[dict[str, Any]] = deepcopy(PROJECTION_BINDINGS)
        for version in self._versions:
            version.setdefault("revision", 1)
            version.setdefault("resolutions", [])
            version.setdefault("cleanse_rules", [])
            version.setdefault("preview", None)
            version.setdefault("superseded_resolutions", [])

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def sample(self) -> dict[str, Any]:
        return deepcopy(SAMPLE_DATASET)

    def options(self) -> dict[str, Any]:
        return {
            "conversions": CONVERSIONS,
            "cleanse_operations": CLEANSE_OPERATIONS,
            "validator_types": ["必填", "唯一"],
            "resolution_strategies": RESOLUTION_STRATEGIES,
            "statuses": [STATUS_DIRTY, STATUS_ACTIVE, STATUS_ARCHIVED],
        }

    def list_versions(self) -> list[dict[str, Any]]:
        return [
            {
                key: deepcopy(version.get(key))
                for key in (
                    "id", "rule_id", "rule_code", "name", "version", "status",
                    "created_at", "updated_at", "updated_by", "published_at",
                    "revision", "sample_id", "note",
                )
            }
            | {
                "mappings": len(version.get("mappings", [])),
                "enabled_mappings": len([m for m in version.get("mappings", []) if m.get("enabled", True)]),
                "cleanse_rules": len(version.get("cleanse_rules", [])),
                "validators": len(version.get("validators", [])),
                "unresolved_conflicts": len([c for c in conflicts_with_status(version) if not c["resolved"]]),
                "has_preview": version.get("preview") is not None,
                "preview_revision": (version.get("preview") or {}).get("revision"),
            }
            for version in self._versions
        ]

    def bindings(self) -> list[dict[str, Any]]:
        return deepcopy(self._bindings)

    def get_version(self, version_id: str) -> dict[str, Any]:
        version = self._find(version_id)
        return _public(version)

    def conflicts(self, version_id: str) -> dict[str, Any]:
        """与预演解耦的冲突清单：编辑映射后仍能看到最新裁决状态。"""
        version = self._find(version_id)
        return {
            "version_id": version_id,
            "revision": version.get("revision"),
            "read_only": version["status"] != STATUS_DIRTY,
            "items": conflicts_with_status(version),
            "warnings": (version.get("preview") or {}).get("warnings", 0),
            "superseded": deepcopy(version.get("superseded_resolutions", [])),
        }

    def _find(self, version_id: str) -> dict[str, Any]:
        for version in self._versions:
            if version["id"] == version_id:
                return version
        raise ProjectionError(f"投影版本 {version_id} 不存在")

    def _editable(self, version_id: str) -> dict[str, Any]:
        version = self._find(version_id)
        if version["status"] not in EDITABLE_STATUS:
            raise ProjectionError(
                f"版本 {version_id} 的状态是「{version['status']}」，已启用版本只读。"
                "请先调用派生草稿，在编辑中版本里修改，现用版本不会被改动。"
            )
        return version

    def _touch(self, version: dict[str, Any], note: str) -> None:
        version["revision"] = int(version.get("revision", 1)) + 1
        version["updated_at"] = _now()
        version["updated_by"] = "值班管理员"
        version["preview"] = None
        version["_last_change"] = note
        # 目标名改动后，指向已消失冲突的裁决不能继续挂着装作“已裁决”；
        # 被清理的裁决留痕在 superseded_resolutions 里，方便解释“刚才的裁决为什么没了”
        live_ids = {conflict["id"] for conflict in build_conflicts(version)}
        stale = [item for item in version.get("resolutions", []) if item.get("conflict_id") not in live_ids]
        if stale:
            version["superseded_resolutions"] = [
                {**item, "superseded_at": _now(), "reason": "冲突已不存在（相关映射改目标、停用或删除）"}
                for item in stale
            ]
        version["resolutions"] = [
            item for item in version.get("resolutions", [])
            if item.get("conflict_id") in live_ids
        ]

    # ------------------------------------------------------------------
    # 草稿派生 / 丢弃
    # ------------------------------------------------------------------
    def fork_draft(self, source_id: str) -> dict[str, Any]:
        source = self._find(source_id)
        if source["status"] == STATUS_DIRTY:
            raise ProjectionError(f"版本 {source_id} 本身已是编辑中版本，无需再派生")
        if source["status"] != STATUS_ACTIVE:
            raise ProjectionError(f"只有已启用版本可以派生编辑草稿，当前状态是「{source['status']}」")
        for existing in self._versions:
            if existing["rule_id"] == source["rule_id"] and existing["status"] == STATUS_DIRTY:
                raise ProjectionError(
                    f"规则 {source['rule_code']} 已有编辑中版本 {existing['id']}，请继续编辑或先丢弃它"
                )
        number = max(int(v["version"]) for v in self._versions if v["rule_id"] == source["rule_id"]) + 1
        draft_id = self._next_id("PV")
        draft = deepcopy(source)
        draft.update({
            "id": draft_id,
            "version": number,
            "status": STATUS_DIRTY,
            "created_at": _now(),
            "updated_at": _now(),
            "published_at": None,
            "revision": 1,
            "note": f"从已启用版本 {source_id} 派生；现用版本保持不变。",
            "preview": None,
            "resolutions": [],
        })
        # 重新生成编辑中的标识，避免和源版本共享 id
        prefix = "M-D"
        suffix = number * 100
        for index, mapping in enumerate(draft.get("mappings", []), start=1):
            mapping["id"] = f"{prefix}{suffix + index}"
        for index, rule in enumerate(draft.get("cleanse_rules", []), start=1):
            rule["id"] = f"C-D{suffix + index}"
        for index, validator in enumerate(draft.get("validators", []), start=1):
            validator["id"] = f"V-D{suffix + index}"
        self._versions.append(draft)
        return _public(draft)

    def discard_draft(self, version_id: str) -> dict[str, Any]:
        version = self._find(version_id)
        if version["status"] != STATUS_DIRTY:
            raise ProjectionError(f"只有编辑中版本可以丢弃，{version_id} 的状态是「{version['status']}」")
        self._versions.remove(version)
        return {"discarded": version_id, "message": f"编辑中版本 {version_id} 已丢弃，未影响任何已启用版本"}

    def create_draft(self, rule_id: int) -> dict[str, Any]:
        source = next(
            (v for v in self._versions if v["rule_id"] == rule_id and v["status"] == STATUS_ACTIVE),
            None,
        )
        if source is None:
            raise ProjectionError(f"规则 {rule_id} 没有已启用版本，无法派生草稿")
        return self.fork_draft(source["id"])

    # ------------------------------------------------------------------
    # 编辑映射
    # ------------------------------------------------------------------
    _MAPPING_REQUIRED = ["source", "target"]

    def _validate_mapping(self, values: dict[str, Any], *, mapping_id: str | None = None) -> dict[str, Any]:
        missing = [
            field for field in self._MAPPING_REQUIRED
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            raise ProjectionError(f"映射缺少字段：{'、'.join(missing)}")
        conversion = str(values.get("convert") or "文本")
        if conversion not in CONVERSIONS:
            raise ProjectionError(f"类型转换「{conversion}」不在允许范围：{'、'.join(CONVERSIONS)}")
        target = str(values.get("target") or "").strip()
        if any(ch in target for ch in " \t\n"):
            raise ProjectionError(f"目标字段名「{target}」不能包含空白字符")
        operation = str(values.get("cleanse") or "无")
        if operation not in CLEANSE_OPERATIONS:
            raise ProjectionError(f"清洗表达式「{operation}」不在允许范围：{'、'.join(CLEANSE_OPERATIONS)}")
        return {
            "id": mapping_id or self._next_id("M"),
            "source": str(values.get("source") or "").strip(),
            "target": target,
            "convert": conversion,
            "default": str(values.get("default") or ""),
            "cleanse": operation,
            "required": bool(values.get("required")),
            "enabled": bool(values.get("enabled", True)),
        }

    def add_mapping(self, version_id: str, values: dict[str, Any]) -> dict[str, Any]:
        version = self._editable(version_id)
        mapping = self._validate_mapping(values)
        version.setdefault("mappings", []).append(mapping)
        self._touch(version, f"新增映射 {mapping['id']}")
        return _public(version)

    def update_mapping(self, version_id: str, mapping_id: str, values: dict[str, Any]) -> dict[str, Any]:
        version = self._editable(version_id)
        for index, mapping in enumerate(version.get("mappings", [])):
            if mapping["id"] == mapping_id:
                merged = dict(mapping)
                merged.update({key: value for key, value in values.items() if key in {
                    "source", "target", "convert", "default", "cleanse", "required", "enabled",
                }})
                validated = self._validate_mapping(merged, mapping_id=mapping_id)
                version["mappings"][index] = validated
                self._touch(version, f"修改映射 {mapping_id}")
                return _public(version)
        raise ProjectionError(f"映射 {mapping_id} 不在版本 {version_id} 中")

    def delete_mapping(self, version_id: str, mapping_id: str) -> dict[str, Any]:
        version = self._editable(version_id)
        before = len(version.get("mappings", []))
        version["mappings"] = [m for m in version.get("mappings", []) if m["id"] != mapping_id]
        if len(version["mappings"]) == before:
            raise ProjectionError(f"映射 {mapping_id} 不在版本 {version_id} 中")
        self._touch(version, f"删除映射 {mapping_id}")
        return _public(version)

    # ------------------------------------------------------------------
    # 编辑清洗规则与校验规则
    # ------------------------------------------------------------------
    def add_cleanse_rule(self, version_id: str, values: dict[str, Any]) -> dict[str, Any]:
        version = self._editable(version_id)
        source = str(values.get("source") or "").strip()
        output = str(values.get("output") or "").strip()
        if not source or not output:
            raise ProjectionError("清洗规则必须填写源字段与输出字段")
        operation = str(values.get("op") or "无")
        if operation == "无":
            raise ProjectionError("清洗规则的清洗表达式不能为「无」")
        if operation not in CLEANSE_OPERATIONS:
            raise ProjectionError(f"清洗表达式「{operation}」不在允许范围：{'、'.join(CLEANSE_OPERATIONS)}")
        rule = {
            "id": self._next_id("C"),
            "source": source,
            "output": output,
            "op": operation,
            "enabled": bool(values.get("enabled", True)),
        }
        version.setdefault("cleanse_rules", []).append(rule)
        self._touch(version, f"新增清洗规则 {rule['id']}")
        return _public(version)

    def update_cleanse_rule(self, version_id: str, rule_id: str, values: dict[str, Any]) -> dict[str, Any]:
        version = self._editable(version_id)
        for index, rule in enumerate(version.get("cleanse_rules", [])):
            if rule["id"] == rule_id:
                merged = dict(rule)
                merged.update({key: value for key, value in values.items() if key in {
                    "source", "output", "op", "enabled",
                }})
                merged["source"] = str(merged.get("source") or "").strip()
                merged["output"] = str(merged.get("output") or "").strip()
                if not merged["source"] or not merged["output"]:
                    raise ProjectionError("清洗规则必须填写源字段与输出字段")
                if str(merged.get("op") or "无") == "无":
                    raise ProjectionError("清洗规则的清洗表达式不能为「无」")
                if str(merged.get("op")) not in CLEANSE_OPERATIONS:
                    raise ProjectionError(f"清洗表达式「{merged.get('op')}」不在允许范围：{'、'.join(CLEANSE_OPERATIONS)}")
                version["cleanse_rules"][index] = merged
                self._touch(version, f"修改清洗规则 {rule_id}")
                return _public(version)
        raise ProjectionError(f"清洗规则 {rule_id} 不在版本 {version_id} 中")

    def delete_cleanse_rule(self, version_id: str, rule_id: str) -> dict[str, Any]:
        version = self._editable(version_id)
        before = len(version.get("cleanse_rules", []))
        version["cleanse_rules"] = [r for r in version.get("cleanse_rules", []) if r["id"] != rule_id]
        if len(version["cleanse_rules"]) == before:
            raise ProjectionError(f"清洗规则 {rule_id} 不在版本 {version_id} 中")
        self._touch(version, f"删除清洗规则 {rule_id}")
        return _public(version)

    def set_validators(self, version_id: str, validators: list[dict[str, Any]]) -> dict[str, Any]:
        version = self._editable(version_id)
        prepared: list[dict[str, Any]] = []
        for index, item in enumerate(validators, start=1):
            target = str(item.get("target") or "").strip()
            vtype = str(item.get("type") or "").strip()
            if not target:
                raise ProjectionError(f"第 {index} 条校验规则缺少校验对象")
            if vtype not in ("必填", "唯一"):
                raise ProjectionError(f"第 {index} 条校验规则的校验类型「{vtype}」必须是必填或唯一")
            prepared.append({
                "id": str(item.get("id") or self._next_id("V")),
                "target": target,
                "type": vtype,
                "blocking": bool(item.get("blocking", True)),
            })
        version["validators"] = prepared
        self._touch(version, "更新校验规则")
        return _public(version)

    # ------------------------------------------------------------------
    # 冲突裁决
    # ------------------------------------------------------------------
    def resolve_conflict(
        self,
        version_id: str,
        conflict_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        version = self._editable(version_id)
        conflict = next((c for c in conflicts_with_status(version) if c["id"] == conflict_id), None)
        if conflict is None:
            raise ProjectionError(
                f"冲突 {conflict_id} 已不存在；可能相关映射已被删除、停用或改了目标名，请刷新预演"
            )
        winner_id = str(values.get("winner_id") or "").strip()
        loser_action = str(values.get("loser_action") or "").strip()
        if not winner_id:
            raise ProjectionError("裁决必须指定保留哪一个写入方")
        if loser_action not in LOSER_ACTIONS:
            raise ProjectionError("裁决必须指定落选字段的处理方式：丢弃或重命名")
        participant_ids = {item["id"] for item in conflict["participants"]}
        if winner_id not in participant_ids:
            raise ProjectionError(f"保留字段「{winner_id}」不在本次冲突的写入方中")
        rename_to = str(values.get("rename_to") or "").strip()
        if loser_action == "rename" and not rename_to:
            raise ProjectionError("选择重命名时必须填写新的目标字段名")
        if loser_action == "drop" and rename_to:
            raise ProjectionError("选择丢弃时不应再填写重命名目标，避免含义含糊")
        # 立即校验重命名不会撞上现有目标或其他冲突的裁决结果
        candidates = {writer["target"] for writer in collect_writers(version)}
        for other in version.get("resolutions", []):
            if other.get("conflict_id") != conflict_id and other.get("loser_action") == "rename":
                candidates.add(str(other.get("rename_to") or ""))
        if loser_action == "rename":
            losers = [item for item in conflict["participants"] if item["id"] != winner_id]
            if len(losers) > 1:
                raise ProjectionError("本次冲突有多个落选字段，不能共用同一个新目标名；请改为丢弃或分别建规则")
            if rename_to == conflict["target"]:
                raise ProjectionError("重命名目标不能与原目标同名")
            if rename_to in candidates:
                raise ProjectionError(f"重命名目标「{rename_to}」已经被其他字段占用，会产生新的重名冲突")
        existing = next(
            (item for item in version.get("resolutions", []) if item.get("conflict_id") == conflict_id),
            None,
        )
        resolution = {
            "conflict_id": conflict_id,
            "target": conflict["target"],
            "winner_id": winner_id,
            "loser_action": loser_action,
            "rename_to": rename_to if loser_action == "rename" else "",
            "decided_at": _now(),
            "decided_by": "值班管理员",
        }
        resolutions = [item for item in version.get("resolutions", []) if item.get("conflict_id") != conflict_id]
        resolutions.append(resolution)
        version["resolutions"] = resolutions
        self._touch(version, f"裁决冲突 {conflict_id}")
        return {
            "version": _public(version),
            "conflict": next(c for c in conflicts_with_status(version) if c["id"] == conflict_id),
            "replaced": existing is not None,
        }

    def clear_resolution(self, version_id: str, conflict_id: str) -> dict[str, Any]:
        version = self._editable(version_id)
        before = len(version.get("resolutions", []))
        version["resolutions"] = [r for r in version.get("resolutions", []) if r.get("conflict_id") != conflict_id]
        if len(version["resolutions"]) == before:
            raise ProjectionError(f"冲突 {conflict_id} 没有已登记的裁决")
        self._touch(version, f"撤销冲突裁决 {conflict_id}")
        return _public(version)

    # ------------------------------------------------------------------
    # 预演与发布
    # ------------------------------------------------------------------
    def preview(self, version_id: str, sample: dict[str, Any] | None = None) -> dict[str, Any]:
        version = self._find(version_id)
        dataset = SAMPLE_DATASET
        if sample is not None:
            dataset = self._validate_sample(sample)
        result = build_preview(version, dataset, _now())
        result["revision"] = version.get("revision")
        result["read_only"] = version["status"] != STATUS_DIRTY
        if result["read_only"]:
            # 已启用/已停用版本只读：可以预演查看口径，但不写入预演记录、也不产生发布资格
            result["publishable"] = False
            result["verdict"] = "只读查看"
            result["verdict_note"] = (
                f"版本 {version_id} 状态是「{version['status']}」，仅供查看，不能编辑或发布；"
                "要调整口径请从该版本派生编辑草稿。"
            )
            return deepcopy(result)
        version["preview"] = {
            "revision": version.get("revision"),
            "generated_at": result["generated_at"],
            "verdict": result["verdict"],
            "publishable": result["publishable"],
            "warnings": result["summary"]["warnings"],
        }
        # 发布时用同一份样本重算，保证“预演看到什么，发布前就再核什么”
        version["_preview_sample"] = deepcopy(dataset)
        version["updated_at"] = _now()
        return deepcopy(result)

    def _validate_sample(self, sample: dict[str, Any]) -> dict[str, Any]:
        records = sample.get("records")
        if not isinstance(records, list) or not records:
            raise ProjectionError("样本必须是包含 records 数组的对象，且至少有一条记录")
        normalized = []
        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                raise ProjectionError(f"样本第 {index} 条记录不是对象")
            if "payload" not in record:
                raise ProjectionError(f"样本第 {index} 条记录缺少 payload，无法读取源字段")
            normalized.append({
                "record_id": str(record.get("record_id") or f"S-{index:03d}"),
                "label": str(record.get("label") or "自定义样本"),
                "payload": record["payload"],
            })
        return {
            "id": str(sample.get("id") or "SAMPLE-CUSTOM"),
            "name": str(sample.get("name") or "自定义样本"),
            "records": normalized,
        }

    def get_preview(self, version_id: str) -> dict[str, Any] | None:
        version = self._find(version_id)
        preview = version.get("preview")
        if preview is None:
            return None
        return deepcopy(preview)

    def publish(self, version_id: str, *, force_on_warning: bool = False) -> dict[str, Any]:
        version = self._find(version_id)
        if version["status"] != STATUS_DIRTY:
            raise ProjectionError(
                f"版本 {version_id} 状态是「{version['status']}」，只有编辑中版本可以发布"
            )
        preview = version.get("preview")
        if preview is None:
            raise ProjectionError("发布前必须先预演；当前版本还没有预演记录")
        if preview.get("revision") != version.get("revision"):
            raise ProjectionError(
                f"预演对应 revision {preview.get('revision')}，版本已修改到 revision {version.get('revision')}，请重新预演"
            )
        if not preview.get("publishable"):
            raise ProjectionError("预演未通过，不能替换现用版本；请按阻断问题清单修正后重新预演")
        if preview.get("verdict") != "通过":
            raise ProjectionError("预演结论不是「通过」，不能发布")
        # 发布时基于同一份样本重算一次，防止预演后被改动
        dataset = version.get("_preview_sample") or SAMPLE_DATASET
        final_preview = build_preview(version, dataset, _now())
        if not final_preview["publishable"]:
            raise ProjectionError(
                "重新计算预演时发现阻断问题：" + "；".join(item["label"] for item in final_preview["blocking_issues"][:3])
            )
        if not force_on_warning and final_preview.get("warnings"):
            warning_labels = "；".join(w["label"] for w in final_preview["warnings"][:3])
            raise ProjectionError(
                f"预演已通过但有 {len(final_preview['warnings'])} 条警告：{warning_labels}。"
                "如确认接受警告，请带 confirm_warnings=true 再发布"
            )
        old_active = [
            item for item in self._versions
            if item["rule_id"] == version["rule_id"] and item["status"] == STATUS_ACTIVE
        ]
        for previous in old_active:
            previous["status"] = STATUS_ARCHIVED
            previous["note"] = (
                f"已被版本 {version['id']}（v{version['version']}）替换；"
                "既有任务绑定保持在本版本，除非显式改绑。"
            )
            previous["updated_at"] = _now()
        version["status"] = STATUS_ACTIVE
        version["published_at"] = _now()
        version["updated_at"] = _now()
        version["note"] = f"由预演 revision {preview['revision']} 发布；现用口径从此版本开始。"
        version["preview"] = None
        return {
            "version": _public(version),
            "archived_versions": [item["id"] for item in old_active],
            "bindings": deepcopy(self._bindings),
            "message": (
                f"版本 {version['id']} 已启用。"
                f"已归档 {len(old_active)} 个旧版本；任务绑定未改动，既有任务仍按原版本口径执行。"
            ),
        }

    # ------------------------------------------------------------------
    # 任务改绑
    # ------------------------------------------------------------------
    def rebind(self, task_code: str, target_version_id: str) -> dict[str, Any]:
        target = self._find(target_version_id)
        if target["status"] != STATUS_ACTIVE:
            raise ProjectionError(
                f"只能把任务改绑到已启用版本；{target_version_id} 当前状态是「{target['status']}」"
            )
        for binding in self._bindings:
            if binding["task_code"] == task_code:
                if binding["rule_id"] != target["rule_id"]:
                    raise ProjectionError(
                        f"任务 {task_code} 绑定的规则是 {binding['rule_code']}，不能改绑到其他规则的版本"
                    )
                if binding["version_id"] == target_version_id:
                    raise ProjectionError(f"任务 {task_code} 已经绑定版本 {target_version_id}，无需改绑")
                previous = {
                    "version_id": binding["version_id"],
                    "version": binding["version"],
                }
                binding.update({
                    "version_id": target["id"],
                    "version": target["version"],
                    "bound_at": _now(),
                    "semantics": (
                        f"显式改绑到 {target['id']}（v{target['version']}）；"
                        f"原绑定 {previous['version_id']}（v{previous['version']}）仍保留历史记录。"
                    ),
                })
                return {"binding": deepcopy(binding), "previous": previous}
        raise ProjectionError(f"任务 {task_code} 没有字段映射绑定，不能改绑")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        """生成全库唯一的标识；历史版本里的 D/A 后缀也一并计入，避免新增项撞号。"""
        used_ids = {
            str(item.get("id"))
            for version in self._versions
            for collection in ("mappings", "cleanse_rules", "validators", "resolutions", "id")
            for item in ([version] if collection == "id" else version.get(collection, []))
            if item.get("id")
        }
        numbers = [
            int(match.group(1))
            for item in used_ids
            if (match := re.search(r"(\d+)$", item))
        ]
        floor = max(numbers, default=self._sequences.get(prefix, 0))
        next_number = max(floor, self._sequences.get(prefix, 0)) + 1
        self._sequences[prefix] = next_number
        return f"{prefix}-{next_number}"


projection_service = ProjectionService()
