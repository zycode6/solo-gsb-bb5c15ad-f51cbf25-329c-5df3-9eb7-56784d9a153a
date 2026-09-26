"""API 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


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


class EditSegmentIn(BaseModel):
    """参与编辑计划的片段（设备本地毫秒），带唯一 id 与单位毫秒成本。"""

    id: str = Field(..., min_length=1, description="片段唯一标识字符串")
    device: str = Field(..., description="片段所属设备名")
    start_ms: float = Field(..., allow_inf_nan=False, description="片段起点（设备本地毫秒）")
    end_ms: float = Field(..., allow_inf_nan=False, description="片段终点（设备本地毫秒）")
    cost_per_ms: float = Field(
        ..., ge=0.0, allow_inf_nan=False, description="单位毫秒成本，有限且非负"
    )


class EditPlanRequest(BaseModel):
    devices: list[str] = Field(..., min_length=1, description="全部录音机设备名")
    reference_device: str = Field(..., description="参考设备，其映射固定为原时间")
    sync_points: list[SyncPointIn] = Field(default_factory=list, description="成对同步点")
    tolerance_ms: float = Field(..., ge=0.0, allow_inf_nan=False, description="非负残差容限（毫秒）")
    segments: list[EditSegmentIn] = Field(default_factory=list, description="候选片段")
    window_start_ms: float = Field(
        ..., allow_inf_nan=False, description="参考时间窗起点（统一时间，毫秒）"
    )
    window_end_ms: float = Field(
        ..., allow_inf_nan=False, description="参考时间窗终点（统一时间，毫秒）"
    )
    switch_cost: float = Field(
        ..., ge=0.0, allow_inf_nan=False, description="相邻小区间更换片段 id 的单次成本，有限且非负"
    )

    @field_validator("devices")
    @classmethod
    def _unique_devices(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("设备列表存在重复项")
        return v

    @field_validator("segments")
    @classmethod
    def _unique_segment_ids(cls, v: list[EditSegmentIn]) -> list[EditSegmentIn]:
        ids = [s.id for s in v]
        if len(set(ids)) != len(ids):
            raise ValueError("片段 id 存在重复")
        return v

    @model_validator(mode="after")
    def _window_ordered(self) -> "EditPlanRequest":
        if not self.window_start_ms < self.window_end_ms:
            raise ValueError("时间窗起点必须小于终点")
        return self


class EditPlanItemOut(BaseModel):
    id: str = Field(..., description="所选片段 id")
    device: str = Field(..., description="片段所属设备")
    start_ms: float = Field(..., description="参考时间起点（毫秒）")
    end_ms: float = Field(..., description="参考时间终点（毫秒）")
    device_start_ms: float = Field(..., description="逆变换后的设备本地起点（毫秒）")
    device_end_ms: float = Field(..., description="逆变换后的设备本地终点（毫秒）")


class EditPlanResponse(BaseModel):
    reference_device: str
    window_start_ms: float
    window_end_ms: float
    total_cost: float = Field(..., description="总成本：时长成本之和 + 切换次数 × switch_cost")
    plan: list[EditPlanItemOut] = Field(
        ..., description="按时间升序、同一片段相邻项已合并的编辑计划"
    )
