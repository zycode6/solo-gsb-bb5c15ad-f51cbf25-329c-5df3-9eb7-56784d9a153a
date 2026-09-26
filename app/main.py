"""FastAPI 入口：多录音机时钟联合标定与剪辑规划服务。"""

from __future__ import annotations

import math

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .calibration import CalibrationError, SyncPoint, calibrate
from .models import (
    CalibrateRequest,
    CalibrateResponse,
    EditPlanRequest,
    EditPlanResponse,
    MappingOut,
    PlanItemOut,
    SegmentOut,
    DevicesBase,
)
from .planner import EditPlanSegment, Gap, build_edit_plan


def _json_safe(value: object) -> object:
    """把错误载荷中的 NaN/Infinity 替换为字符串，避免严格 JSON 响应 500。"""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value

app = FastAPI(
    title="多录音机时钟联合标定与剪辑规划服务",
    version="1.1.0",
    description=(
        "对离线采访素材的多台录音机做时钟联合标定：参考设备映射固定为原时间，"
        "其余设备采用正斜率仿射映射，联立全部同步点做最小二乘。"
        "edit-plan 接口在同一标定结果上，按参考时间窗求解全局最小成本的剪辑计划；"
        "服务只处理时间数值，不读写音频文件。"
    ),
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # 标准 422 结构；输入中的 NaN/Infinity 会被转为字符串以保证响应合法。
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(jsonable_encoder(exc.errors()))},
    )


def _validate_devices_and_segments(req: DevicesBase) -> None:
    """两个接口共用的请求级校验：未知设备与片段起止颠倒返回 422。"""
    known = set(req.devices)

    if req.reference_device not in known:
        raise HTTPException(
            status_code=422,
            detail=f"参考设备 '{req.reference_device}' 不在设备列表中",
        )
    for i, seg in enumerate(req.segments):
        if seg.device not in known:
            raise HTTPException(
                status_code=422,
                detail=f"第 {i} 个片段引用了未知设备 '{seg.device}'",
            )
        if seg.start_ms > seg.end_ms:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"第 {i} 个片段起止颠倒：start_ms={seg.start_ms} "
                    f"大于 end_ms={seg.end_ms}"
                ),
            )


def _run_calibration(req: DevicesBase):
    """复用同一套标定参数与求解器；标定失败沿用原错误（422）。"""
    try:
        return calibrate(
            devices=req.devices,
            reference_device=req.reference_device,
            sync_points=[
                SyncPoint(sp.device_a, sp.time_a_ms, sp.device_b, sp.time_b_ms)
                for sp in req.sync_points
            ],
            tolerance_ms=req.tolerance_ms,
        )
    except CalibrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/calibrate", response_model=CalibrateResponse)
def calibrate_endpoint(req: CalibrateRequest) -> CalibrateResponse:
    _validate_devices_and_segments(req)
    result = _run_calibration(req)

    unified = [
        SegmentOut(
            device=seg.device,
            label=seg.label,
            start_ms=result.mappings[seg.device][0] * seg.start_ms
            + result.mappings[seg.device][1],
            end_ms=result.mappings[seg.device][0] * seg.end_ms
            + result.mappings[seg.device][1],
        )
        for seg in req.segments
    ]
    unified.sort(key=lambda s: s.start_ms)

    return CalibrateResponse(
        reference_device=req.reference_device,
        mappings={
            d: MappingOut(slope=slope, intercept_ms=intercept)
            for d, (slope, intercept) in result.mappings.items()
        },
        segments=unified,
        sync_point_residuals_ms=result.residuals_ms,
        max_residual_ms=max(result.residuals_ms, default=0.0),
    )


@app.post("/v1/edit-plan", response_model=EditPlanResponse)
def edit_plan_endpoint(req: EditPlanRequest) -> EditPlanResponse:
    _validate_devices_and_segments(req)
    result = _run_calibration(req)

    plan_or_gaps = build_edit_plan(
        mappings=result.mappings,
        segments=[
            EditPlanSegment(
                id=seg.id,
                device=seg.device,
                local_start_ms=seg.start_ms,
                local_end_ms=seg.end_ms,
                cost_per_ms=seg.cost_per_ms,
            )
            for seg in req.segments
        ],
        window_start_ms=req.window_start_ms,
        window_end_ms=req.window_end_ms,
        switch_cost=req.switch_cost,
    )

    if isinstance(plan_or_gaps, list):
        gaps: list[Gap] = plan_or_gaps
        raise HTTPException(
            status_code=422,
            detail={
                "error": "uncovered_gaps",
                "message": "参考时间窗内存在无任何片段完整覆盖的空档",
                "gaps": [
                    {"start_ms": g.start_ms, "end_ms": g.end_ms} for g in gaps
                ],
            },
        )

    return EditPlanResponse(
        total_cost=plan_or_gaps.total_cost,
        plan=[
            PlanItemOut(
                id=item.id,
                device=item.device,
                ref_start_ms=item.ref_start_ms,
                ref_end_ms=item.ref_end_ms,
                local_start_ms=item.local_start_ms,
                local_end_ms=item.local_end_ms,
            )
            for item in plan_or_gaps.items
        ],
    )
