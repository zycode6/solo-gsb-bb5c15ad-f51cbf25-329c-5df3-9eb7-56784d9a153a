"""多录音机时钟联合标定核心逻辑。

模型：参考设备的映射固定为恒等（统一时间 = 参考设备原时间），
其余每台设备 i 使用正斜率仿射映射把本地毫秒读数换算到统一时间：

    unified_ms = slope_i * device_ms + intercept_i      (slope_i > 0)

所有同步点联立为线性方程组，用 NumPy 最小二乘求解全部未知
(slope_i, intercept_i)。每个同步点 (u, t_u, v, t_v) 贡献一条方程：

    mapped_u(t_u) - mapped_v(t_v) = 0

其中参考设备的 mapped 项是已知常数，移项到右端。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


class CalibrationError(Exception):
    """标定问题无可接受解时抛出，消息说明拒绝原因。"""


@dataclass(frozen=True)
class SyncPoint:
    """同一物理时刻在两台设备上的毫秒读数。"""

    device_a: str
    time_a_ms: float
    device_b: str
    time_b_ms: float


@dataclass
class CalibrationResult:
    # device -> (slope, intercept_ms)，参考设备固定为 (1.0, 0.0)
    mappings: dict[str, tuple[float, float]]
    # 每个同步点在统一时间下的绝对残差（毫秒），顺序与输入一致
    residuals_ms: list[float] = field(default_factory=list)


def calibrate(
    devices: list[str],
    reference_device: str,
    sync_points: list[SyncPoint],
    tolerance_ms: float,
) -> CalibrationResult:
    """联立全部同步点做最小二乘标定，失败时抛出 CalibrationError。"""
    if reference_device not in devices:
        raise CalibrationError(f"参考设备 '{reference_device}' 不在设备列表中")
    if tolerance_ms < 0:
        raise CalibrationError(f"残差容限必须非负，收到 {tolerance_ms}")

    for sp in sync_points:
        for d in (sp.device_a, sp.device_b):
            if d not in devices:
                raise CalibrationError(f"同步点引用了未知设备 '{d}'")
        if sp.device_a == sp.device_b:
            raise CalibrationError(f"同步点的两台设备不能相同（'{sp.device_a}'）")

    mappings: dict[str, tuple[float, float]] = {reference_device: (1.0, 0.0)}
    unknown_devices = [d for d in devices if d != reference_device]

    if unknown_devices:
        # 未知向量 x = [slope_1, intercept_1, slope_2, intercept_2, ...]
        col = {d: 2 * i for i, d in enumerate(unknown_devices)}
        n_unknowns = 2 * len(unknown_devices)
        rows: list[np.ndarray] = []
        rhs: list[float] = []
        for sp in sync_points:
            row = np.zeros(n_unknowns)
            target = 0.0
            for device, t, sign in (
                (sp.device_a, sp.time_a_ms, 1.0),
                (sp.device_b, sp.time_b_ms, -1.0),
            ):
                if device == reference_device:
                    target -= sign * t  # 已知常数移项到右端
                else:
                    j = col[device]
                    row[j] += sign * t
                    row[j + 1] += sign
            rows.append(row)
            rhs.append(target)

        a_mat = np.vstack(rows) if rows else np.zeros((0, n_unknowns))
        b_vec = np.asarray(rhs, dtype=float)

        rank = int(np.linalg.matrix_rank(a_mat)) if a_mat.size else 0
        if rank < n_unknowns:
            raise CalibrationError(
                f"同步点方程组不满列秩（秩 {rank} < 未知数 {n_unknowns}），"
                "无法唯一确定所有设备的映射，请补充与参考设备连通的同步点"
            )

        solution, *_ = np.linalg.lstsq(a_mat, b_vec, rcond=None)
        for d in unknown_devices:
            j = col[d]
            slope, intercept = float(solution[j]), float(solution[j + 1])
            if not np.isfinite(slope) or slope <= 0.0:
                raise CalibrationError(
                    f"设备 '{d}' 拟合斜率非正（{slope!r}），与录音机时钟正向走时不符，拒绝标定"
                )
            if not np.isfinite(intercept):
                raise CalibrationError(f"设备 '{d}' 拟合截距非有限值，拒绝标定")
            mappings[d] = (slope, intercept)

    residuals: list[float] = []
    for sp in sync_points:
        sa, ia = mappings[sp.device_a]
        sb, ib = mappings[sp.device_b]
        residuals.append(abs((sa * sp.time_a_ms + ia) - (sb * sp.time_b_ms + ib)))

    if residuals:
        worst = max(residuals)
        if worst > tolerance_ms:
            bad = residuals.index(worst)
            raise CalibrationError(
                f"第 {bad} 个同步点残差 {worst:.3f} ms 超过容限 {tolerance_ms} ms，拒绝标定"
            )

    return CalibrationResult(mappings=mappings, residuals_ms=residuals)
