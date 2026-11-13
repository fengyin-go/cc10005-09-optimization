"""字段投影预演引擎（纯函数，不依赖存储与 HTTP）。

预演按字段逐步展开：源字段取值 → 默认填充 → 类型转换 → 清洗 → 写入目标。
任何一步失败都会记录原因；转换失败得到的是 None 而不是空字符串，避免下游把失败当成功。
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

CONVERSIONS = ["文本", "整数", "小数", "日期", "数组文本", "布尔"]
CLEANSE_OPERATIONS = ["无", "去首尾空白", "去除分隔符", "标签去空项", "去除货币符号", "日期规范化为连字符", "全角转半角"]
VALIDATOR_TYPES = ["必填", "唯一"]
LOSER_ACTIONS = {"drop": "丢弃", "rename": "重命名"}
CONFLICT_KIND_LABELS = {
    "multi_source_target": "多个源字段写入同一目标",
    "target_vs_cleanse": "目标名与清洗输出重名",
    "cleanse_output_duplicate": "多条清洗规则写入同一输出",
}
RESOLUTION_STRATEGIES = [
    {"value": "drop", "label": "丢弃落选字段"},
    {"value": "rename", "label": "把落选字段重命名为新目标"},
]

FULLWIDTH_TABLE = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
)


# ---------------------------------------------------------------------------
# 取值与类型工具
# ---------------------------------------------------------------------------
def read_path(payload: Any, path: str) -> dict[str, Any]:
    """按点号路径读取嵌套值，并说明在哪一层断掉。支持数组下标。"""
    if not str(path or "").strip():
        return {"found": False, "value": None, "reason": "源字段路径为空"}
    current = payload
    walked: list[str] = []
    for segment in str(path).split("."):
        segment = segment.strip()
        if segment == "":
            return {"found": False, "value": None, "reason": f"路径「{path}」含有空段"}
        if isinstance(current, dict):
            if segment not in current:
                where = ".".join(walked) or "记录根节点"
                return {
                    "found": False,
                    "value": None,
                    "reason": f"{where} 中不存在键「{segment}」",
                }
            current = current[segment]
        elif isinstance(current, list):
            if not segment.isdigit():
                where = ".".join(walked) or "记录根节点"
                return {
                    "found": False,
                    "value": None,
                    "reason": f"{where} 是数组，需要用数字下标访问「{segment}」",
                }
            index = int(segment)
            if index >= len(current):
                where = ".".join(walked) or "记录根节点"
                return {
                    "found": False,
                    "value": None,
                    "reason": f"{where} 长度为 {len(current)}，下标 {index} 越界",
                }
            current = current[index]
        else:
            where = ".".join(walked) or "记录根节点"
            return {
                "found": False,
                "value": None,
                "reason": f"{where} 是{raw_type(current)}，不是对象或数组，无法继续读取「{segment}」",
            }
        walked.append(segment)
    return {"found": True, "value": current, "reason": ""}


def raw_type(value: Any) -> str:
    if value is None:
        return "空值"
    if isinstance(value, bool):
        return "布尔"
    if isinstance(value, int):
        return "整数"
    if isinstance(value, float):
        return "小数"
    if isinstance(value, str):
        return "字符串"
    if isinstance(value, list):
        return "数组"
    if isinstance(value, dict):
        return "对象"
    return type(value).__name__


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _display(value: Any, limit: int = 80) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


# ---------------------------------------------------------------------------
# 类型转换
# ---------------------------------------------------------------------------

def _convert_date(value: Any) -> tuple[bool, Any, str]:
    if isinstance(value, bool):
        return False, None, f"布尔值 {_display(value)} 不能作为日期"
    if isinstance(value, (int, float)):
        # 20240102 这类整数按 YYYYMMDD 解释；其余数字没有可靠的日期含义
        if isinstance(value, float) and not value.is_integer():
            return False, None, f"小数 {value} 不能作为日期"
        text = str(int(value))
        if not re.fullmatch(r"\d{8}", text):
            return False, None, f"整数 {text} 不能作为日期，请使用 YYYYMMDD 或日期字符串"
        return _parse_date_text(text)
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return _parse_date_text(text)
    # 明确识别 2024-13-40 这类越界日期，给出具体原因
    if re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", text):
        parts = re.split(r"[-/.]", text)
        try:
            date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError as exc:
            return False, None, f"“{text}” 不是有效日期：{exc}"
        return True, date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat(), ""
    return False, None, f"“{text}” 不符合 YYYY-MM-DD、YYYY/MM/DD 或 YYYYMMDD"


def _parse_date_text(text: str) -> tuple[bool, Any, str]:
    try:
        parsed = datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        return False, None, f"“{text}” 不是有效日期：{exc}"
    return True, parsed.date().isoformat(), ""


def _convert_integer(value: Any) -> tuple[bool, Any, str]:
    if isinstance(value, bool):
        return False, None, f"布尔值 {_display(value)} 不能当作整数（混合类型不隐式转换）"
    if isinstance(value, int):
        return True, value, ""
    if isinstance(value, float):
        if not math.isfinite(value):
            return False, None, f"非有限小数 {_display(value)} 不能转为整数"
        if value.is_integer():
            return True, int(value), ""
        return False, None, f"小数 {value} 不能无损转为整数"
    text = str(value).strip().replace(",", "").replace("，", "")
    if re.fullmatch(r"[+-]?\d+", text):
        return True, int(text), ""
    if re.fullmatch(r"[+-]?\d+\.0+", text):
        return True, int(Decimal(text)), ""
    return False, None, f"“{text}” 不是可解析的整数"


def _convert_decimal(value: Any) -> tuple[bool, Any, str]:
    if isinstance(value, bool):
        return False, None, f"布尔值 {_display(value)} 不能当作小数（混合类型不隐式转换）"
    if isinstance(value, (int, float)):
        result = float(value)
        if not math.isfinite(result):
            return False, None, f"非有限数值 {result} 不能作为小数"
        return True, result, ""
    text = str(value).strip()
    text = text.replace("，", "").replace(",", "").replace("￥", "").replace("¥", "").replace("$", "")
    text = text.replace("元", "").strip()
    if not text:
        return False, None, "去掉货币符号后为空"
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return False, None, f"“{text}” 不是可解析的小数"
    if not parsed.is_finite():
        return False, None, f"“{text}” 不是有限数值"
    return True, float(parsed), ""


def _convert_boolean(value: Any) -> tuple[bool, Any, str]:
    if isinstance(value, bool):
        return True, value, ""
    if isinstance(value, int) and value in (0, 1):
        return True, bool(value), ""
    text = str(value).strip().lower()
    if text in {"true", "1", "是", "y", "yes"}:
        return True, True, ""
    if text in {"false", "0", "否", "n", "no"}:
        return True, False, ""
    return False, None, f"“{text}” 无法识别为布尔值"


def _convert_text(value: Any) -> tuple[bool, Any, str]:
    if isinstance(value, (list, dict)):
        return False, None, f"{raw_type(value)}不能直接转为文本，请改用「数组文本」"
    if isinstance(value, bool):
        return True, "true" if value else "false", ""
    return True, str(value), ""


def _convert_array_text(value: Any) -> tuple[bool, Any, str]:
    if isinstance(value, list):
        items = [str(item) for item in value if item is not None and str(item).strip() != ""]
        return True, ",".join(items), ""
    if isinstance(value, dict):
        return False, None, "对象不能直接转为数组文本"
    if isinstance(value, str):
        return True, value, ""
    return False, None, f"{raw_type(value)}不能转为数组文本"


_CONVERTERS = {
    "文本": _convert_text,
    "整数": _convert_integer,
    "小数": _convert_decimal,
    "日期": _convert_date,
    "数组文本": _convert_array_text,
    "布尔": _convert_boolean,
}


def convert_value(value: Any, conversion: str) -> dict[str, Any]:
    converter = _CONVERTERS.get(conversion)
    if converter is None:
        return {"ok": False, "value": None, "reason": f"未知类型转换「{conversion}」"}
    try:
        ok, result, reason = converter(value)
    except Exception as exc:  # noqa: BLE001 - 预演需要把意外也转成可读原因
        return {"ok": False, "value": None, "reason": f"转换器异常：{exc}"}
    return {"ok": ok, "value": result, "reason": reason}


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------
def cleanse_value(value: Any, operation: str) -> dict[str, Any]:
    """返回 status: applied / none / skipped / failed，以及清洗后的值。"""
    if operation in ("", "无"):
        return {"status": "none", "value": value, "reason": "未配置清洗表达式"}
    if value is None:
        return {"status": "skipped", "value": None, "reason": "值为空，跳过清洗"}
    if operation == "去首尾空白":
        if not isinstance(value, str):
            return {"status": "skipped", "value": value, "reason": f"非文本值（{raw_type(value)}），不做首尾去空白"}
        cleaned = value.strip()
        return {"status": "applied", "value": cleaned, "reason": f"去掉首尾空白，长度 {len(value)} → {len(cleaned)}"}
    if operation == "去除分隔符":
        if not isinstance(value, str):
            return {"status": "skipped", "value": value, "reason": f"非文本值（{raw_type(value)}），不做分隔符处理"}
        cleaned = re.sub(r"[-—–\s​]", "", value)
        return {"status": "applied", "value": cleaned, "reason": "去掉连字符、破折号与空白"}
    if operation == "标签去空项":
        if isinstance(value, list):
            items = [str(item).strip() for item in value if item is not None and str(item).strip()]
            return {"status": "applied", "value": ",".join(items), "reason": f"数组按逗号拼接，丢弃 {len(value) - len(items)} 个空项"}
        if isinstance(value, str):
            before = value.count(",") + 1 if value else 0
            items = [item.strip() for item in value.split(",") if item.strip()]
            return {"status": "applied", "value": ",".join(items), "reason": f"按逗号拆分并去掉空项，{before} 项 → {len(items)} 项"}
        return {"status": "failed", "value": None, "reason": f"标签去空项需要数组或字符串，实际是{raw_type(value)}"}
    if operation == "去除货币符号":
        if not isinstance(value, str):
            return {"status": "skipped", "value": value, "reason": f"非文本值（{raw_type(value)}），不做货币符号处理"}
        cleaned = re.sub(r"[￥¥$,\s，元]", "", value)
        return {"status": "applied", "value": cleaned, "reason": "去掉货币符号、千分位逗号与“元”"}
    if operation == "日期规范化为连字符":
        if not isinstance(value, str):
            return {"status": "skipped", "value": value, "reason": f"非文本值（{raw_type(value)}），不做日期分隔符规范化"}
        cleaned = re.sub(r"[./]", "-", value.strip())
        return {"status": "applied", "value": cleaned, "reason": "把日期分隔符统一为连字符"}
    if operation == "全角转半角":
        if not isinstance(value, str):
            return {"status": "skipped", "value": value, "reason": f"非文本值（{raw_type(value)}），不做全角转换"}
        cleaned = value.translate(FULLWIDTH_TABLE)
        return {"status": "applied", "value": cleaned, "reason": "全角数字与字母转为半角"}
    return {"status": "failed", "value": None, "reason": f"未知清洗表达式「{operation}」"}


# ---------------------------------------------------------------------------
# 冲突识别
# ---------------------------------------------------------------------------
def _writer_for_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": mapping["id"],
        "kind": "mapping",
        "source": mapping.get("source", ""),
        "target": mapping.get("target", ""),
        "label": f"映射 {mapping['id']}（{mapping.get('source', '')} → {mapping.get('target', '')}）",
        "required": bool(mapping.get("required")),
    }


def _writer_for_cleanse(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rule["id"],
        "kind": "cleanse",
        "source": rule.get("source", ""),
        "target": rule.get("output", ""),
        "label": f"清洗规则 {rule['id']}（{rule.get('source', '')} → {rule.get('output', '')}）",
        "required": False,
    }


def collect_writers(version: dict[str, Any]) -> list[dict[str, Any]]:
    writers: list[dict[str, Any]] = []
    for mapping in version.get("mappings", []):
        if mapping.get("enabled", True) and str(mapping.get("target") or "").strip():
            writers.append(_writer_for_mapping(mapping))
    for rule in version.get("cleanse_rules", []):
        if rule.get("enabled", True) and str(rule.get("output") or "").strip():
            writers.append(_writer_for_cleanse(rule))
    return writers


def build_conflicts(version: dict[str, Any]) -> list[dict[str, Any]]:
    """按目标名分组识别写入冲突；每条冲突都必须有人工裁决才能继续。"""
    writers = collect_writers(version)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for writer in writers:
        grouped.setdefault(writer["target"], []).append(writer)

    resolutions = {item.get("conflict_id"): item for item in version.get("resolutions", []) if item.get("conflict_id")}
    conflicts: list[dict[str, Any]] = []
    for target, participants in sorted(grouped.items()):
        if len(participants) < 2:
            continue
        kinds = {item["kind"] for item in participants}
        if kinds == {"mapping"}:
            kind = "multi_source_target"
        elif kinds == {"cleanse"}:
            kind = "cleanse_output_duplicate"
        else:
            kind = "target_vs_cleanse"
        conflict_id = f"CF-{target}"
        resolution = resolutions.get(conflict_id)
        conflicts.append({
            "id": conflict_id,
            "target": target,
            "kind": kind,
            "kind_label": CONFLICT_KIND_LABELS[kind],
            "participants": participants,
            "resolution": resolution,
        })
    return conflicts


def _resolution_problem(
    conflict: dict[str, Any],
    resolution: dict[str, Any] | None,
    used_names: set[str],
) -> str:
    if not resolution:
        return "尚未裁决：多个写入方会争用同一目标，系统不会按顺序静默覆盖"
    participant_ids = {item["id"] for item in conflict["participants"]}
    winner = str(resolution.get("winner_id") or "").strip()
    if winner not in participant_ids:
        return f"裁决指定的保留字段「{winner or '空'}」不在本次冲突的写入方中"
    losers = [item for item in conflict["participants"] if item["id"] != winner]
    action = str(resolution.get("loser_action") or "").strip()
    if action not in LOSER_ACTIONS:
        return "裁决必须明确落选字段是「丢弃」还是「重命名」"
    if action == "rename":
        new_name = str(resolution.get("rename_to") or "").strip()
        if len(losers) > 1:
            return "本次冲突有多个落选字段，不能共用同一个新目标名；请改为丢弃或分别建规则"
        if not new_name:
            return "选择重命名时必须填写新的目标字段名"
        if new_name == conflict["target"]:
            return "重命名目标不能与原目标同名"
        if new_name in used_names:
            return f"重命名目标「{new_name}」已经被其他字段占用，会产生新的重名冲突"
    return ""


def conflicts_with_status(version: dict[str, Any]) -> list[dict[str, Any]]:
    """带裁决状态的冲突列表，供列表页统计和抽屉展示使用。"""
    return plan_writes(version)["conflicts"]


def plan_writes(version: dict[str, Any]) -> dict[str, Any]:
    """把冲突与裁决转换成写入计划；未裁决/裁决无效的目标全部挂起。"""
    conflicts = build_conflicts(version)
    writers = collect_writers(version)
    all_names = {writer["target"] for writer in writers}
    rename_names: set[str] = set()

    blocked_targets: set[str] = set()
    dropped: set[str] = set()
    renamed: dict[str, str] = {}
    resolution_notes: dict[str, str] = {}
    for conflict in conflicts:
        target = conflict["target"]
        resolution = conflict.get("resolution")
        # 重命名目标要避让所有既有目标名以及其他重命名结果
        used = (all_names - {target}) | rename_names
        problem = _resolution_problem(conflict, resolution, used)
        conflict["resolved"] = not problem
        conflict["resolution_problem"] = problem
        if problem:
            blocked_targets.add(target)
            resolution_notes[conflict["id"]] = problem
            continue
        winner = resolution["winner_id"]
        action = resolution["loser_action"]
        for participant in conflict["participants"]:
            if participant["id"] == winner:
                continue
            if action == "drop":
                dropped.add(participant["id"])
            else:
                new_name = resolution["rename_to"].strip()
                renamed[participant["id"]] = new_name
                rename_names.add(new_name)
        resolution_notes[conflict["id"]] = f"保留「{winner}」，其余按{ LOSER_ACTIONS[action] }处理"

    return {
        "conflicts": conflicts,
        "writers": writers,
        "blocked_targets": blocked_targets,
        "dropped_writers": dropped,
        "renamed_writers": renamed,
        "notes": resolution_notes,
    }


# ---------------------------------------------------------------------------
# 单字段预演步骤
# ---------------------------------------------------------------------------
def _step(key: str, label: str, status: str, value: Any = None, note: str = "", **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "value": value, "note": note, **extra}


def _mapping_steps(
    payload: Any,
    mapping: dict[str, Any],
) -> dict[str, Any]:
    """展开映射的五个步骤，返回步骤、最终值与失败信息。"""
    path = str(mapping.get("source") or "")
    read = read_path(payload, path)
    source_status = "ok"
    source_note = path
    if not read["found"]:
        source_status = "missing"
        source_note = f"路径 {path} 读取失败：{read['reason']}"
    elif is_blank(read["value"]):
        source_status = "blank"
        source_note = "字段存在，但值为空" if read["value"] is not None else "字段存在，但值为 null"
    source_step = _step(
        "source", "源字段取值", source_status,
        read["value"] if read["found"] else None,
        source_note,
        value_type=raw_type(read["value"]),
    )

    blank = (not read["found"]) or is_blank(read["value"])
    default = str(mapping.get("default") or "")
    value = read["value"] if read["found"] else None
    if blank:
        if default != "":
            value = default
            default_step = _step(
                "default", "默认填充", "applied", default,
                f"源值为空，使用默认填充值「{default}」",
                value_type=raw_type(value),
            )
        else:
            value = None
            default_step = _step(
                "default", "默认填充", "no_default", None,
                "源值为空且未配置默认填充；不会用空字符串顶替",
            )
    else:
        default_step = _step(
            "default", "默认填充", "not_needed", value,
            "源值非空，不触发默认填充",
            value_type=raw_type(value),
        )

    conversion = str(mapping.get("convert") or "文本")
    if value is None:
        convert_step = _step(
            "convert", "类型转换", "skipped", None,
            f"没有可用值，跳过「{conversion}」转换",
            from_type=raw_type(read["value"]), to_type=conversion,
        )
        converted_ok = True
    else:
        result = convert_value(value, conversion)
        converted_ok = bool(result["ok"])
        convert_step = _step(
            "convert", "类型转换", "ok" if converted_ok else "failed",
            result["value"] if converted_ok else None,
            f"{raw_type(value)} → {conversion}" if converted_ok else f"转换失败：{result['reason']}；不使用空字符串顶替",
            from_type=raw_type(value), to_type=conversion, failure_reason="" if converted_ok else result["reason"],
        )
        value = result["value"] if converted_ok else None

    operation = str(mapping.get("cleanse") or "无")
    if value is None:
        cleanse_step = _step(
            "cleanse", "清洗", "skipped", None,
            "没有可清洗的值，跳过清洗表达式",
            operation=operation,
        )
        cleanse_ok = True
    else:
        cleaned = cleanse_value(value, operation)
        cleanse_ok = cleaned["status"] != "failed"
        cleanse_step = _step(
            "cleanse", "清洗", cleaned["status"],
            cleaned["value"] if cleanse_ok else None,
            f"清洗「{operation}」：{cleaned['reason']}",
            operation=operation, before=value,
        )
        value = cleaned["value"] if cleanse_ok else None

    return {
        "steps": [source_step, default_step, convert_step, cleanse_step],
        "value": value,
        "read_blank": blank,
        "conversion_failure": not converted_ok,
        "cleanse_failure": not cleanse_ok,
        "convert_reason": convert_step.get("failure_reason", ""),
    }


def _cleanse_steps(payload: Any, rule: dict[str, Any]) -> dict[str, Any]:
    path = str(rule.get("source") or "")
    read = read_path(payload, path)
    if not read["found"]:
        source_step = _step("source", "源字段取值", "missing", None, f"路径 {path} 读取失败：{read['reason']}", value_type=raw_type(None))
    elif is_blank(read["value"]):
        source_step = _step("source", "源字段取值", "blank", read["value"], "字段存在但为空", value_type=raw_type(read["value"]))
    else:
        source_step = _step("source", "源字段取值", "ok", read["value"], path, value_type=raw_type(read["value"]))
    operation = str(rule.get("op") or "无")
    if not read["found"] or is_blank(read["value"]):
        cleanse_step = _step("cleanse", "清洗", "skipped", None, "源值为空，清洗规则不产出", operation=operation)
        return {
            "steps": [source_step, cleanse_step],
            "value": None,
            "read_blank": True,
            "conversion_failure": False,
            "cleanse_failure": False,
            "convert_reason": "",
        }
    cleaned = cleanse_value(read["value"], operation)
    ok = cleaned["status"] != "failed"
    cleanse_step = _step(
        "cleanse", "清洗", cleaned["status"], cleaned["value"] if ok else None,
        f"清洗「{operation}」：{cleaned['reason']}", operation=operation, before=read["value"],
    )
    return {
        "steps": [source_step, cleanse_step],
        "value": cleaned["value"] if ok else None,
        "read_blank": False,
        "conversion_failure": False,
        "cleanse_failure": not ok,
        "convert_reason": "",
    }


# ---------------------------------------------------------------------------
# 记录与校验
# ---------------------------------------------------------------------------
def _assign_outcome(
    *,
    writer: dict[str, Any],
    target: str,
    computed: dict[str, Any],
    plan: dict[str, Any],
    record_values: dict[str, Any],
) -> dict[str, Any]:
    steps = list(computed["steps"])
    writer_id = writer["id"]
    effective_target = plan["renamed_writers"].get(writer_id, target)
    reason = ""
    status = "written"
    cause = ""
    if writer_id in plan["dropped_writers"]:
        status = "dropped"
        cause = "resolution"
        reason = f"冲突裁决：目标「{target}」由保留字段写入，本字段按裁决丢弃"
    elif target in plan["blocked_targets"]:
        status = "held"
        cause = "conflict"
        reason = f"目标「{target}」存在未裁决冲突，系统未写入任何候选值，等待人工裁决"
    elif computed["conversion_failure"]:
        cause = "conversion"
        if writer.get("required"):
            status = "rejected"
            reason = f"必填字段类型转换失败：{computed['convert_reason']}；拒收该记录，不使用空字符串补齐"
        else:
            status = "dropped"
            reason = f"类型转换失败：{computed['convert_reason']}；字段被丢弃，不使用空字符串顶替"
    elif computed["cleanse_failure"]:
        cause = "cleanse"
        if writer.get("required"):
            status = "rejected"
            reason = "必填字段清洗失败；拒收该记录"
        else:
            status = "dropped"
            reason = "清洗失败；字段被丢弃"
    elif is_blank(computed["value"]):
        cause = "missing"
        if writer.get("required"):
            status = "rejected"
            reason = "必填字段没有取到有效值（源为空或清洗后为空，且默认填充为空），拒收该记录；不使用空字符串顶替"
        else:
            status = "empty"
            reason = "源值为空或清洗后为空且没有默认填充；未写入目标（不会写成空字符串）"

    if status == "written":
        record_values[effective_target] = computed["value"]
    assign_step = _step(
        "assign", "写入目标", status, computed["value"] if status == "written" else None,
        reason or f"写入目标「{effective_target}」",
        target=effective_target,
    )
    steps.append(assign_step)
    return {
        "writer_id": writer_id,
        "kind": writer["kind"],
        "source": writer["source"],
        "declared_target": target,
        "effective_target": effective_target,
        "status": status,
        "cause": cause,
        "value": computed["value"] if status == "written" else None,
        "reason": reason,
        "steps": steps,
    }


def _validation_payload(
    validators: list[dict[str, Any]],
    accepted_records: list[dict[str, Any]],
    mapping_targets: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    for validator in validators:
        target = str(validator.get("target") or "")
        vtype = str(validator.get("type") or "")
        blocker = bool(validator.get("blocking"))
        result: dict[str, Any] = {
            "validator_id": validator.get("id"),
            "target": target,
            "type": vtype,
            "blocking": blocker,
            "checked": len(accepted_records),
            "failures": [],
        }
        if target not in mapping_targets:
            result["status"] = "failed" if blocker else "warning"
            result["note"] = f"没有启用中的映射或清洗规则写入目标「{target}」"
            if blocker:
                blocking.append({
                    "kind": "validation",
                    "label": f"校验规则 {validator.get('id')}（{vtype}）",
                    "reason": result["note"],
                })
            results.append(result)
            continue

        if vtype == "必填":
            failures = []
            for record in accepted_records:
                value = record["values"].get(target)
                if is_blank(value):
                    failures.append({
                        "record_id": record["record_id"],
                        "record_label": record.get("record_label"),
                        "value": value,
                        "reason": "目标字段缺失或为空",
                    })
            result["status"] = "failed" if failures else "passed"
            result["failures"] = failures
            if failures and blocker:
                blocking.append({
                    "kind": "validation",
                    "label": f"必填校验「{target}」",
                    "reason": f"{len(failures)} 条通过记录缺少该字段：" + "、".join(item["record_id"] for item in failures),
                })
        elif vtype == "唯一":
            groups: dict[str, list[dict[str, Any]]] = {}
            for record in accepted_records:
                value = record["values"].get(target)
                if is_blank(value):
                    continue
                key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
                groups.setdefault(key, []).append(record)
            duplicate_groups = {key: records for key, records in groups.items() if len(records) > 1}
            failures = []
            for key, records in duplicate_groups.items():
                ids = "、".join(item["record_id"] for item in records)
                for record in records:
                    failures.append({
                        "record_id": record["record_id"],
                        "record_label": record.get("record_label"),
                        "value": record["values"].get(target),
                        "reason": f"值「{key}」在 {ids} 中重复",
                    })
            result["status"] = "failed" if failures else "passed"
            result["failures"] = failures
            if failures and blocker:
                blocking.append({
                    "kind": "validation",
                    "label": f"唯一性校验「{target}」",
                    "reason": f"{len(duplicate_groups)} 组重复值：" + "、".join(duplicate_groups),
                })
        else:
            result["status"] = "warning"
            result["note"] = f"未知校验类型「{vtype}」"
            results.append(result)
            continue
        results.append(result)
    return results, blocking


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build_preview(version: dict[str, Any], dataset: dict[str, Any], generated_at: str) -> dict[str, Any]:
    plan = plan_writes(version)
    conflicts = plan["conflicts"]
    writers = plan["writers"]

    mapping_by_id = {mapping["id"]: mapping for mapping in version.get("mappings", [])}

    # 校验目标：所有实际可能写入的目标名（含落选字段重命名后的目标）
    written_targets: dict[str, str] = {}
    for writer in writers:
        if writer["id"] in plan["dropped_writers"]:
            continue
        target = plan["renamed_writers"].get(writer["id"], writer["target"])
        if writer["target"] in plan["blocked_targets"]:
            continue
        written_targets[target] = writer["id"]

    records: list[dict[str, Any]] = []
    field_issues: list[dict[str, Any]] = []
    blocked_issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for conflict in conflicts:
        if not conflict["resolved"]:
            blocked_issues.append({
                "kind": "conflict",
                "label": f"{conflict['kind_label']}：{conflict['target']}",
                "reason": conflict.get("resolution_problem") or "冲突未裁决",
                "conflict_id": conflict["id"],
            })

    for record in dataset.get("records", []):
        values: dict[str, Any] = {}
        outcomes: list[dict[str, Any]] = []
        reject_reasons: list[str] = []
        payload = record.get("payload")
        for writer in writers:
            if writer["kind"] == "mapping":
                mapping = mapping_by_id.get(writer["id"], {})
                computed = _mapping_steps(payload, mapping)
            else:
                rule = next((item for item in version.get("cleanse_rules", []) if item["id"] == writer["id"]), {})
                computed = _cleanse_steps(payload, rule)
            outcome = _assign_outcome(
                writer=writer,
                target=writer["target"],
                computed=computed,
                plan=plan,
                record_values=values,
            )
            outcomes.append(outcome)
            if outcome["status"] in ("dropped", "rejected", "empty", "held"):
                issue = {
                    "record_id": record.get("record_id"),
                    "record_label": record.get("label"),
                    "channel": writer["kind"],
                    "writer_id": writer["id"],
                    "source": writer["source"],
                    "target": outcome["effective_target"],
                    "declared_target": writer["target"],
                    "status": outcome["status"],
                    "cause": outcome["cause"],
                    "stage": _last_failed_stage(outcome["steps"]),
                    "reason": outcome["reason"],
                    "value": outcome["value"],
                }
                field_issues.append(issue)
            if outcome["status"] == "rejected":
                reject_reasons.append(f"{outcome['effective_target']}：{outcome['reason']}")

        accepted = not reject_reasons
        records.append({
            "record_id": record.get("record_id"),
            "record_label": record.get("label"),
            "accepted": accepted,
            "reject_reasons": reject_reasons,
            "values": values,
            "outcomes": outcomes,
        })
        if reject_reasons:
            warnings.append({
                "kind": "record_rejected",
                "label": f"记录 {record.get('record_id')} 被拒收",
                "reason": "；".join(reject_reasons),
            })

    accepted_records = [item for item in records if item["accepted"]]
    validations, validation_blocking = _validation_payload(
        version.get("validators", []),
        accepted_records,
        written_targets,
    )
    blocked_issues.extend(validation_blocking)

    for validation in validations:
        if validation.get("status") in ("warning", "failed"):
            if not (validation.get("status") == "failed" and validation.get("blocking")):
                warnings.append({
                    "kind": "validation",
                    "label": f"{validation.get('type')}校验「{validation.get('target')}」",
                    "reason": validation.get("note") or f"{len(validation.get('failures', []))} 条记录未通过",
                })
    for issue in field_issues:
        if issue["status"] == "dropped" and issue.get("cause") != "resolution":
            warnings.append({
                "kind": "field_dropped",
                "label": f"{issue['record_id']} 的「{issue['target']}」被丢弃",
                "reason": issue["reason"],
            })
    disabled_mappings = [
        {
            "id": mapping["id"],
            "source": mapping.get("source"),
            "target": mapping.get("target"),
            "reason": "草稿中已停用该映射，不参与投影",
        }
        for mapping in version.get("mappings", [])
        if not mapping.get("enabled", True)
    ]
    disabled_cleanse_rules = [
        {
            "id": rule["id"],
            "source": rule.get("source"),
            "target": rule.get("output"),
            "reason": "草稿中已停用该清洗规则，不参与投影",
        }
        for rule in version.get("cleanse_rules", [])
        if not rule.get("enabled", True)
    ]

    summary = {
        "records": len(records),
        "accepted": len(accepted_records),
        "rejected": len(records) - len(accepted_records),
        "mappings": len(version.get("mappings", [])),
        "enabled_mappings": len([m for m in version.get("mappings", []) if m.get("enabled", True)]),
        "cleanse_rules": len(version.get("cleanse_rules", [])),
        "validators": len(validations),
        "written_fields": sum(1 for record in records for outcome in record["outcomes"] if outcome["status"] == "written"),
        "dropped_fields": sum(1 for issue in field_issues if issue["status"] == "dropped"),
        "rejected_fields": sum(1 for issue in field_issues if issue["status"] == "rejected"),
        "empty_fields": sum(1 for issue in field_issues if issue["status"] == "empty"),
        "held_fields": sum(1 for issue in field_issues if issue["status"] == "held"),
        "conflicts": len(conflicts),
        "unresolved_conflicts": sum(1 for conflict in conflicts if not conflict["resolved"]),
        "blocking_issues": len(blocked_issues),
        "warnings": len(warnings),
    }
    publishable = not blocked_issues
    if publishable:
        verdict_note = "预演通过：冲突均已裁决，没有阻断问题；发布后既有任务绑定仍按原版本执行。"
    elif blocked_issues:
        verdict_note = "预演未通过：" + "；".join(issue["label"] for issue in blocked_issues[:3])
        if len(blocked_issues) > 3:
            verdict_note += f" 等 {len(blocked_issues)} 项阻断问题"
    else:
        verdict_note = "预演未通过"
    return {
        "version_id": version.get("id"),
        "version": version.get("version"),
        "rule_id": version.get("rule_id"),
        "rule_code": version.get("rule_code"),
        "revision": version.get("revision"),
        "sample_id": dataset.get("id"),
        "sample_name": dataset.get("name"),
        "generated_at": generated_at,
        "conflicts": conflicts,
        "records": records,
        "validations": validations,
        "field_issues": field_issues,
        "disabled_mappings": disabled_mappings,
        "disabled_cleanse_rules": disabled_cleanse_rules,
        "blocking_issues": blocked_issues,
        "warnings": warnings,
        "summary": summary,
        "publishable": publishable,
        "verdict": "通过" if publishable else "未通过",
        "verdict_note": verdict_note,
    }


def _last_failed_stage(steps: list[dict[str, Any]]) -> str:
    """给出问题首次出现的环节，优先展示转换/清洗失败，其次是没有可选值的来源。"""
    body = steps[:-1]
    for step in body:
        if step["status"] == "failed":
            return step["label"]
    for step in body:
        if step["status"] in ("missing", "blank", "no_default"):
            return step["label"]
    cleansed = next((step for step in body if step["key"] == "cleanse"), None)
    if cleansed and cleansed.get("value") in (None, "") and cleansed.get("status") in ("applied", "skipped"):
        return "清洗"
    return "写入目标"
