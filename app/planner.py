"""剪辑规划核心：标定换算后按参考时间窗做最小成本选片。

流程：
1. 由 ``calibration.calibrate`` 得到各设备本地时间 → 统一（参考）时间的
   正斜率仿射映射 ``u = slope * t + intercept``；
2. 把每个片段的本地端点换算到统一时间，并与参考时间窗
   ``[window_start_ms, window_end_ms)`` 求交，丢弃空片段；
3. 以全部（裁剪后的）片段端点和窗口端点排序去重，划分半开小区间
   ``[x_k, x_{k+1})``；
4. 每个小区间必须被至少一个片段 *完整覆盖*（片段起点 <= 区间起点且
   片段终点 >= 区间终点），没有任何片段覆盖的连续小区间合并为空档；
5. 全部区间都有覆盖时，用动态规划全局最小化：

       Σ 区间时长_ms × 所选片段 cost_per_ms
       + 相邻区间所选片段 id 变化次数 × switch_cost

   总成本相同时，选择逐区间 id 序列字典序最小的方案；
6. 将相邻且 id 相同的已选区间合并为一个计划项，计划项同时给出参考时间
   起止和经逆变换 ``t = (u - intercept) / slope`` 得到的设备本地起止。

本模块只做数值计算，不触碰任何音频文件。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EditPlanSegment:
    """规划输入片段：时间为设备本地毫秒。"""

    id: str
    device: str
    local_start_ms: float
    local_end_ms: float
    cost_per_ms: float


@dataclass(frozen=True)
class Clip:
    """换算并裁剪到参考时间窗之后的片段（统一时间毫秒）。"""

    id: str
    device: str
    start_ms: float
    end_ms: float
    cost_per_ms: float


@dataclass(frozen=True)
class PlanItem:
    """合并后的计划项。"""

    id: str
    device: str
    ref_start_ms: float
    ref_end_ms: float
    local_start_ms: float
    local_end_ms: float


@dataclass(frozen=True)
class Gap:
    """参考时间窗内无任何片段完整覆盖的连续空档。"""

    start_ms: float
    end_ms: float


@dataclass(frozen=True)
class EditPlan:
    total_cost: float
    items: list[PlanItem]


def _to_clips(
    mappings: dict[str, tuple[float, float]],
    segments: list[EditPlanSegment],
    window_start_ms: float,
    window_end_ms: float,
) -> list[Clip]:
    """把片段换算到统一时间并裁剪到窗口，丢弃零长度片段。"""
    clips: list[Clip] = []
    for seg in segments:
        slope, intercept = mappings[seg.device]
        unified_start = slope * seg.local_start_ms + intercept
        unified_end = slope * seg.local_end_ms + intercept
        clipped_start = max(unified_start, window_start_ms)
        clipped_end = min(unified_end, window_end_ms)
        if clipped_end > clipped_start:
            clips.append(
                Clip(
                    id=seg.id,
                    device=seg.device,
                    start_ms=clipped_start,
                    end_ms=clipped_end,
                    cost_per_ms=seg.cost_per_ms,
                )
            )
    return clips


def build_edit_plan(
    mappings: dict[str, tuple[float, float]],
    segments: list[EditPlanSegment],
    window_start_ms: float,
    window_end_ms: float,
    switch_cost: float,
) -> EditPlan | list[Gap]:
    """计算最小编辑计划。

    返回 :class:`EditPlan`；若窗口内存在空档，则返回合并后的
    :class:`Gap` 列表（按起点升序，互不重叠）。
    """
    clips = _to_clips(mappings, segments, window_start_ms, window_end_ms)

    # 仿射变换与最小二乘会带来亚纳秒级浮点误差，使本相接的端点错位成
    # 极短空档/碎片区间。以相对容差把近重合端点对齐：窗口端点保持精确，
    # 片段端点落入容差内则吸附到窗口端点；其余端点按容差分组合并。
    eps = 1e-10 * max(
        1.0, abs(window_start_ms), abs(window_end_ms), *(abs(c) for clip in clips for c in (clip.start_ms, clip.end_ms))
    )

    def _snap(v: float) -> float:
        if abs(v - window_start_ms) <= eps:
            return window_start_ms
        if abs(v - window_end_ms) <= eps:
            return window_end_ms
        return v

    clips = [
        Clip(
            id=clip.id,
            device=clip.device,
            start_ms=_snap(clip.start_ms),
            end_ms=_snap(clip.end_ms),
            cost_per_ms=clip.cost_per_ms,
        )
        for clip in clips
    ]

    raw = sorted(
        {
            v
            for clip in clips
            for v in (clip.start_ms, clip.end_ms)
            if v != window_start_ms and v != window_end_ms
        }
    )
    inner: list[float] = []
    for v in raw:
        if inner and v - inner[-1] <= eps:
            continue  # 与已保留端点近重合，并入该端点
        inner.append(v)

    points = sorted({window_start_ms, window_end_ms, *inner})
    intervals = list(zip(points[:-1], points[1:]))

    # cover[k]：完整覆盖第 k 个小区间的片段（Clip 下标）
    cover: list[list[int]] = []
    gaps: list[Gap] = []
    gap_start: float | None = None
    gap_end: float = window_start_ms
    for k, (lo, hi) in enumerate(intervals):
        candidates = [
            i
            for i, clip in enumerate(clips)
            if clip.start_ms <= lo and clip.end_ms >= hi
        ]
        cover.append(candidates)
        if not candidates:
            if gap_start is None:
                gap_start = lo
            gap_end = hi  # 相邻无覆盖小区间首尾相接，直接延长空档
        elif gap_start is not None:
            gaps.append(Gap(gap_start, gap_end))
            gap_start = None
    if gap_start is not None:
        gaps.append(Gap(gap_start, gap_end))
    if gaps:
        return gaps

    # 后缀 DP：suffix[k][i] = 第 k 个区间选片段 i 时，k..n-1 区间的最小成本。
    # 后缀阶段只保留每个首片段下的最小成本；同价方案的 id 字典序比较放到
    # 前向重建阶段（前缀固定时即等价于全局序列字典序）。
    n = len(intervals)
    suffix: list[dict[int, float]] = [dict() for _ in range(n + 1)]
    for k in range(n - 1, -1, -1):
        lo, hi = intervals[k]
        duration = hi - lo
        next_costs = suffix[k + 1]
        for i in cover[k]:
            own_cost = duration * clips[i].cost_per_ms
            if not next_costs:
                suffix[k][i] = own_cost
            else:
                suffix[k][i] = min(
                    rest
                    + own_cost
                    + (switch_cost if j != i else 0.0)
                    for j, rest in next_costs.items()
                )

    # 前向重建：从 suffix[0] 中按 (成本, id) 取最小，随后逐区间选择所有仍能
    # 达成该最优成本的下一区间 id 中字典序最小者。
    first = min(suffix[0], key=lambda i: (suffix[0][i], clips[i].id))
    chosen: list[int] = []
    i = first
    for k in range(n):
        chosen.append(i)
        if k == n - 1:
            break
        lo, hi = intervals[k]
        own_cost = (hi - lo) * clips[i].cost_per_ms
        optimal = suffix[k][i]

        def _matches(j: int, _own: float = own_cost, _opt: float = optimal) -> bool:
            candidate = suffix[k + 1][j] + _own + (switch_cost if j != i else 0.0)
            # 同一最优值经不同运算路径可能有微小舍入差异，用相对容差判等。
            return abs(candidate - _opt) <= 1e-10 * max(
                1.0, abs(candidate), abs(_opt)
            )

        feasible = [j for j in suffix[k + 1] if _matches(j)]
        i = min(feasible, key=lambda j: clips[j].id)

    # 合并相邻且 id 相同的区间（请求层已保证 id 唯一，即同一个片段）。
    merged: list[tuple[int, float, float]] = []
    for k, clip_idx in enumerate(chosen):
        lo, hi = intervals[k]
        if merged and clips[merged[-1][0]].id == clips[clip_idx].id:
            # 与上一项同 id：把终点延长到当前区间终点（保留原起点）。
            merged[-1] = (merged[-1][0], merged[-1][1], hi)
        else:
            merged.append((clip_idx, lo, hi))

    items: list[PlanItem] = []
    for clip_idx, lo, hi in merged:
        clip = clips[clip_idx]
        slope, intercept = mappings[clip.device]
        items.append(
            PlanItem(
                id=clip.id,
                device=clip.device,
                ref_start_ms=lo,
                ref_end_ms=hi,
                local_start_ms=(lo - intercept) / slope,
                local_end_ms=(hi - intercept) / slope,
            )
        )

    return EditPlan(total_cost=suffix[0][chosen[0]], items=items)
