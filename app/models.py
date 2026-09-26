"""API 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class SyncPointIn(BaseModel):
    """同一物理时刻在两台设备上的毫秒读数。"""

    device_a: str = Field(..., description="第一台设备名")
    time_a_ms: float = Field(..., allow_inf_nan=False, description="该时刻在 device_a 上的毫秒读数")
    device_b: str = Field(..., description="第二台设备名")
    time_b_ms: float = Field(..., allow_inf_nan=False, description="同一时刻在 device_b 上的毫秒读数")


class SegmentIn(BaseModel):
    """某台设备上的一段采访素材（设备本地毫秒）。"""

    device: str = Field(..., description="片段所属设备名")
    start_ms: float = Field(..., allow_inf_nan=False, description="片段起点（设备本地毫秒）")
    end_ms: float = Field(..., allow_inf_nan=False, description="片段终点（设备本地毫秒）")
    label: str | None = Field(default=None, description="可选的片段标签")


class CalibrateRequest(BaseModel):
    devices: list[str] = Field(..., min_length=1, description="全部录音机设备名")
    reference_device: str = Field(..., description="参考设备，其映射固定为原时间")
    sync_points: list[SyncPointIn] = Field(default_factory=list, description="成对同步点")
    tolerance_ms: float = Field(..., ge=0.0, allow_inf_nan=False, description="非负残差容限（毫秒）")
    segments: list[SegmentIn] = Field(default_factory=list, description="待换算的片段")

    @field_validator("devices")
    @classmethod
    def _unique_devices(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("设备列表存在重复项")
        return v


class MappingOut(BaseModel):
    slope: float = Field(..., description="仿射映射斜率（参考设备固定为 1）")
    intercept_ms: float = Field(..., description="仿射映射截距，毫秒（参考设备固定为 0）")


class SegmentOut(BaseModel):
    device: str
    label: str | None
    start_ms: float = Field(..., description="统一时间下的片段起点（毫秒）")
    end_ms: float = Field(..., description="统一时间下的片段终点（毫秒）")


class CalibrateResponse(BaseModel):
    reference_device: str
    mappings: dict[str, MappingOut] = Field(..., description="各设备的映射系数")
    segments: list[SegmentOut] = Field(..., description="统一时间下的片段清单，按起点升序")
    sync_point_residuals_ms: list[float] = Field(..., description="各同步点残差，顺序与请求一致")
    max_residual_ms: float = Field(..., description="最大同步点残差")
