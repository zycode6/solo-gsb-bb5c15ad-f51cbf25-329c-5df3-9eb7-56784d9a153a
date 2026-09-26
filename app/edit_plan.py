"""编辑计划优化：在参考时间窗内为每个小区间挑选覆盖片段。

标定换算后的片段先裁剪到参考时间窗，再用全部片段端点与窗口端点把窗口
划分为半开小区间 [p_i, p_{i+1})。每个小区间必须选择一个完整覆盖它的片段，
全局最小化：

    Σ 区间时长 × 所选片段单位毫秒成本
    + 相邻区间所选片段 id 变化次数 × switch_cost

同价时取逐区间 id 序列字典序最小者。任一小区间没有完整覆盖它的片段即
构成空档，抛出 GapError 并附带合并后的空档范围。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanSegment:
    """已换算到参考时间轴的候选片段及其设备映射。"""

    seg_id: str
    device: str
    slope: float
    intercept_ms: float
    start_ms: float  # 参考时间起点（未裁剪）
    end_ms: float  # 参考时间终点（未裁剪）
    cost_per_ms: float


@dataclass(frozen=True)
class PlanItem:
    """计划中连续使用同一片段的一段。"""

    seg_id: str
    device: str
    start_ms: float  # 参考时间起点
    end_ms: float  # 参考时间终点
    device_start_ms: float  # 逆变换后的设备本地起点
    device_end_ms: float  # 逆变换后的设备本地终点


@dataclass(frozen=True)
class EditPlan:
    total_cost: float
    items: list[PlanItem]


class GapError(Exception):
    """窗口内存在没有任何片段完整覆盖的空档。"""

    def __init__(self, gaps: list[tuple[float, float]]) -> None:
        self.gaps = gaps
        joined = ", ".join(f"[{a}, {b})" for a, b in gaps)
        super().__init__(f"窗口内存在无片段覆盖的空档：{joined}")


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """把首尾相接（或重叠）的范围合并成连续范围，输入按起点升序。"""
    merged: list[list[float]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _make_item(seg: PlanSegment, start_ms: float, end_ms: float) -> PlanItem:
    return PlanItem(
        seg_id=seg.seg_id,
        device=seg.device,
        start_ms=start_ms,
        end_ms=end_ms,
        device_start_ms=(start_ms - seg.intercept_ms) / seg.slope,
        device_end_ms=(end_ms - seg.intercept_ms) / seg.slope,
    )


def build_edit_plan(
    segments: list[PlanSegment],
    window_start_ms: float,
    window_end_ms: float,
    switch_cost: float,
) -> EditPlan:
    """求解窗口内总成本最低的片段选择，存在空档时抛 GapError。"""
    if not window_start_ms < window_end_ms:
        raise ValueError("时间窗起点必须小于终点")
    if switch_cost < 0:
        raise ValueError("切换成本必须非负")

    # 裁剪到窗口，丢弃与窗口无正长度交集的片段
    clipped: list[tuple[PlanSegment, float, float]] = []
    for seg in segments:
        lo = max(seg.start_ms, window_start_ms)
        hi = min(seg.end_ms, window_end_ms)
        if lo < hi:
            clipped.append((seg, lo, hi))

    # 用窗口端点与全部（裁剪后）片段端点划分半开小区间
    points = {window_start_ms, window_end_ms}
    for _, lo, hi in clipped:
        points.add(lo)
        points.add(hi)
    cuts = sorted(points)
    intervals = list(zip(cuts, cuts[1:]))

    # 每个小区间的候选：完整覆盖它的片段；无候选即为空档
    candidates: list[list[int]] = []
    gap_ranges: list[tuple[float, float]] = []
    for a, b in intervals:
        cand = [j for j, (_, lo, hi) in enumerate(clipped) if lo <= a and hi >= b]
        if not cand:
            gap_ranges.append((a, b))
        candidates.append(cand)
    if gap_ranges:
        raise GapError(_merge_ranges(gap_ranges))

    ids = [seg.seg_id for seg, _, _ in clipped]
    costs = [seg.cost_per_ms for seg, _, _ in clipped]
    durations = [b - a for a, b in intervals]

    # 动态规划：prev[j] = (最低成本, 同价下字典序最小的逐区间 id 序列)，
    # 表示处理到当前区间且当前区间选择 clipped[j]。转移时枚举上一区间
    # 候选，id 不同则加一次 switch_cost；同价保留前缀序列字典序最小者。
    prev: list[tuple[float, tuple[str, ...]] | None] = [None] * len(clipped)
    for j in candidates[0]:
        prev[j] = (durations[0] * costs[j], (ids[j],))
    for i in range(1, len(intervals)):
        cur: list[tuple[float, tuple[str, ...]] | None] = [None] * len(clipped)
        for j in candidates[i]:
            best: tuple[float, tuple[str, ...]] | None = None
            for k in candidates[i - 1]:
                pk = prev[k]
                assert pk is not None  # 上一区间的候选必有状态
                cost = pk[0] + (switch_cost if ids[k] != ids[j] else 0.0)
                if best is None or cost < best[0] or (cost == best[0] and pk[1] < best[1]):
                    best = (cost, pk[1])
            assert best is not None
            cur[j] = (best[0] + durations[i] * costs[j], best[1] + (ids[j],))
        prev = cur

    total_cost, best_seq = min(
        (state for j in candidates[-1] if (state := prev[j]) is not None),
        key=lambda state: (state[0], state[1]),
    )

    # 相邻区间选择同一片段的合并为一个计划项（结果天然按时间升序）
    by_id = {seg.seg_id: seg for seg, _, _ in clipped}
    items: list[PlanItem] = []
    run_id = best_seq[0]
    run_start = intervals[0][0]
    for i in range(1, len(intervals)):
        if best_seq[i] != run_id:
            items.append(_make_item(by_id[run_id], run_start, intervals[i - 1][1]))
            run_id = best_seq[i]
            run_start = intervals[i][0]
    items.append(_make_item(by_id[run_id], run_start, intervals[-1][1]))

    return EditPlan(total_cost=total_cost, items=items)
