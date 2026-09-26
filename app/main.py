"""FastAPI 入口：多录音机时钟联合标定服务。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .calibration import CalibrationError, SyncPoint, calibrate
from .models import (
    CalibrateRequest,
    CalibrateResponse,
    MappingOut,
    SegmentOut,
)

app = FastAPI(
    title="多录音机时钟联合标定服务",
    version="1.0.0",
    description=(
        "对离线采访素材的多台录音机做时钟联合标定：参考设备映射固定为原时间，"
        "其余设备采用正斜率仿射映射，联立全部同步点做最小二乘。"
    ),
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/calibrate", response_model=CalibrateResponse)
def calibrate_endpoint(req: CalibrateRequest) -> CalibrateResponse:
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

    try:
        result = calibrate(
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
