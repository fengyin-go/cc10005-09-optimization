"""字段投影接口：版本隔离、草稿编辑、冲突裁决、分步预演与发布闸门。

所有编辑都只作用于「编辑中」版本；已启用版本只读。发布必须先通过预演，
并且预演结果要和当前 revision 一致，避免“预演通过、内容已改”的含糊替换。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services.projection import ProjectionError, projection_service

router = APIRouter(prefix="/api/projection", tags=["字段投影"])


class MappingPayload(BaseModel):
    source: str | None = None
    target: str | None = None
    convert: str | None = None
    default: str | None = None
    cleanse: str | None = None
    required: bool | None = None
    enabled: bool | None = None


class CleanseRulePayload(BaseModel):
    source: str | None = None
    output: str | None = None
    op: str | None = None
    enabled: bool | None = None


class ConflictResolvePayload(BaseModel):
    winner_id: str
    loser_action: str
    rename_to: str | None = None


class PreviewPayload(BaseModel):
    sample: dict[str, Any] | None = None


class PublishPayload(BaseModel):
    confirm_warnings: bool = False


class RebindingPayload(BaseModel):
    task_code: str
    version_id: str


class VersionCreatePayload(BaseModel):
    rule_id: int = Field(description="字段映射规则 id，派生其已启用版本的编辑草稿")


def _fail(message: str) -> ActionResult:
    return ActionResult(ok=False, message=message)


@router.get("/meta")
def meta() -> dict[str, Any]:
    """投影编辑器的枚举、样本与任务绑定，供页面下拉和隔离说明使用。"""
    return {
        "module": "projection",
        "sample": projection_service.sample(),
        "options": projection_service.options(),
        "bindings": projection_service.bindings(),
    }


@router.get("/versions")
def list_versions() -> dict[str, Any]:
    """列出所有投影版本：已启用只读，编辑中可改，已停用为历史归档。"""
    return {"items": projection_service.list_versions()}


@router.get("/versions/{version_id}")
def get_version(version_id: str) -> dict[str, Any]:
    try:
        return projection_service.get_version(version_id)
    except ProjectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/versions/{version_id}/conflicts")
def list_conflicts(version_id: str) -> dict[str, Any]:
    """独立查询冲突与裁决状态，不依赖预演结果是否还在页面上。"""
    try:
        return projection_service.conflicts(version_id)
    except ProjectionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/versions/{version_id}/fork", response_model=ActionResult)
def fork_draft(version_id: str) -> ActionResult:
    try:
        draft = projection_service.fork_draft(version_id)
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(
        ok=True,
        message=f"已从 {version_id} 派生编辑草稿 {draft['id']}；现用版本保持不变",
        entry=draft,
    )


@router.post("/versions/{version_id}/discard", response_model=ActionResult)
def discard_draft(version_id: str) -> ActionResult:
    try:
        result = projection_service.discard_draft(version_id)
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(ok=True, message=result["message"])


@router.post("/versions", response_model=ActionResult)
def create_draft(payload: VersionCreatePayload) -> ActionResult:
    try:
        draft = projection_service.create_draft(payload.rule_id)
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(ok=True, message=f"已创建编辑草稿 {draft['id']}", entry=draft)


# ---------------------------------------------------------------------------
# 映射编辑（只允许编辑中版本）
# ---------------------------------------------------------------------------
@router.post("/versions/{version_id}/mappings", response_model=ActionResult)
def add_mapping(version_id: str, payload: MappingPayload) -> ActionResult:
    try:
        changed = projection_service.add_mapping(
            version_id,
            {key: value for key, value in payload.model_dump().items() if value is not None},
        )
    except ProjectionError as exc:
        return _fail(str(exc))
    mapping_id = changed["mappings"][-1]["id"]
    return ActionResult(ok=True, message=f"映射 {mapping_id} 已加入编辑草稿", entry=changed)


@router.put("/versions/{version_id}/mappings/{mapping_id}", response_model=ActionResult)
def update_mapping(version_id: str, mapping_id: str, payload: MappingPayload) -> ActionResult:
    try:
        changed = projection_service.update_mapping(
            version_id,
            mapping_id,
            {key: value for key, value in payload.model_dump().items() if value is not None},
        )
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(ok=True, message=f"映射 {mapping_id} 已更新，旧预演结果失效，请重新预演", entry=changed)


@router.delete("/versions/{version_id}/mappings/{mapping_id}", response_model=ActionResult)
def delete_mapping(version_id: str, mapping_id: str) -> ActionResult:
    try:
        changed = projection_service.delete_mapping(version_id, mapping_id)
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(ok=True, message=f"映射 {mapping_id} 已从编辑草稿移除", entry=changed)


@router.post("/versions/{version_id}/cleanse-rules", response_model=ActionResult)
def add_cleanse_rule(version_id: str, payload: CleanseRulePayload) -> ActionResult:
    try:
        changed = projection_service.add_cleanse_rule(
            version_id,
            {key: value for key, value in payload.model_dump().items() if value is not None},
        )
    except ProjectionError as exc:
        return _fail(str(exc))
    rule_id = changed["cleanse_rules"][-1]["id"]
    return ActionResult(ok=True, message=f"清洗规则 {rule_id} 已加入编辑草稿", entry=changed)


@router.put("/versions/{version_id}/cleanse-rules/{rule_id}", response_model=ActionResult)
def update_cleanse_rule(version_id: str, rule_id: str, payload: CleanseRulePayload) -> ActionResult:
    try:
        changed = projection_service.update_cleanse_rule(
            version_id,
            rule_id,
            {key: value for key, value in payload.model_dump().items() if value is not None},
        )
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(ok=True, message=f"清洗规则 {rule_id} 已更新，旧预演结果失效，请重新预演", entry=changed)


@router.delete("/versions/{version_id}/cleanse-rules/{rule_id}", response_model=ActionResult)
def delete_cleanse_rule(version_id: str, rule_id: str) -> ActionResult:
    try:
        changed = projection_service.delete_cleanse_rule(version_id, rule_id)
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(ok=True, message=f"清洗规则 {rule_id} 已从编辑草稿移除", entry=changed)


@router.put("/versions/{version_id}/validators", response_model=ActionResult)
def set_validators(version_id: str, payload: dict[str, Any]) -> ActionResult:
    try:
        changed = projection_service.set_validators(version_id, list(payload.get("validators") or []))
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(ok=True, message="校验规则已更新，旧预演结果失效，请重新预演", entry=changed)


# ---------------------------------------------------------------------------
# 冲突裁决
# ---------------------------------------------------------------------------
@router.post("/versions/{version_id}/conflicts/{conflict_id}/resolve", response_model=ActionResult)
def resolve_conflict(version_id: str, conflict_id: str, payload: ConflictResolvePayload) -> ActionResult:
    try:
        result = projection_service.resolve_conflict(
            version_id,
            conflict_id,
            payload.model_dump(),
        )
    except ProjectionError as exc:
        return _fail(str(exc))
    loser_text = f"重命名为「{payload.rename_to}」" if payload.loser_action == "rename" else "丢弃"
    return ActionResult(
        ok=True,
        message=f"冲突 {conflict_id} 已裁决：保留 {payload.winner_id}，落选字段{loser_text}",
        entry=result["version"],
    )


@router.delete("/versions/{version_id}/conflicts/{conflict_id}/resolve", response_model=ActionResult)
def clear_resolution(version_id: str, conflict_id: str) -> ActionResult:
    try:
        changed = projection_service.clear_resolution(version_id, conflict_id)
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(ok=True, message=f"冲突 {conflict_id} 的裁决已撤销，预演将重新阻断", entry=changed)


# ---------------------------------------------------------------------------
# 预演与发布
# ---------------------------------------------------------------------------
@router.post("/versions/{version_id}/preview", response_model=ActionResult)
def run_preview(version_id: str, payload: PreviewPayload | None = None) -> ActionResult:
    """逐字段展开预演；未裁决冲突与校验失败会进入阻断清单，不允许发布。"""
    try:
        result = projection_service.preview(
            version_id,
            payload.sample if payload else None,
        )
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(
        ok=result["publishable"],
        message=result["verdict_note"],
        entry=result,
    )


@router.post("/versions/{version_id}/publish", response_model=ActionResult)
def publish(version_id: str, payload: PublishPayload | None = None) -> ActionResult:
    try:
        result = projection_service.publish(
            version_id,
            force_on_warning=bool(payload and payload.confirm_warnings),
        )
    except ProjectionError as exc:
        return _fail(str(exc))
    entry = {
        "version": result["version"],
        "archived_versions": result["archived_versions"],
        "bindings": result["bindings"],
    }
    return ActionResult(ok=True, message=result["message"], entry=entry)


@router.get("/bindings")
def bindings() -> dict[str, Any]:
    """既有的任务绑定：发布新版本不会自动改写，需显式改绑。"""
    return {"items": projection_service.bindings()}


@router.post("/bindings/rebind", response_model=ActionResult)
def rebind(payload: RebindingPayload) -> ActionResult:
    try:
        result = projection_service.rebind(payload.task_code, payload.version_id)
    except ProjectionError as exc:
        return _fail(str(exc))
    return ActionResult(
        ok=True,
        message=(
            f"任务 {payload.task_code} 已从 {result['previous']['version_id']} 改绑到 "
            f"{result['binding']['version_id']}；这是显式改绑，不会影响其他任务"
        ),
        entry=result["binding"],
    )
