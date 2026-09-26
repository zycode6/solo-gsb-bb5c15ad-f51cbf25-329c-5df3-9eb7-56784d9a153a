"""剪辑规划核心（app.planner）的行为测试。"""

from __future__ import annotations

import pytest

from app.planner import EditPlanSegment, Gap, build_edit_plan

IDENTITY = {"A": (1.0, 0.0), "B": (1.0, 0.0)}


def _seg(id, device, lo, hi, cost):
    return EditPlanSegment(id, device, lo, hi, cost)


def test_picks_cheapest_segment_for_single_interval():
    res = build_edit_plan(
        IDENTITY,
        [_seg("hi", "A", 0, 100, 2.0), _seg("lo", "B", 0, 100, 1.0)],
        0,
        100,
        5.0,
    )
    assert res.total_cost == pytest.approx(100.0)
    assert [i.id for i in res.items] == ["lo"]
    item = res.items[0]
    assert item.device == "B"
    assert (item.ref_start_ms, item.ref_end_ms) == (0.0, 100.0)
    assert (item.local_start_ms, item.local_end_ms) == (0.0, 100.0)


def test_cheaper_with_gap_vs_pricy_full_with_switch_tradeoff():
    # 区间：0-60 {a1,b}，60-80 {b}，80-100 {a2,b}
    segs = [
        _seg("a1", "A", 0, 60, 1.0),
        _seg("a2", "A", 80, 100, 1.0),
        _seg("b", "B", 0, 100, 2.0),
    ]
    # 低切换成本：a1 -> b -> a2 = 60*1 + 20*2 + 20*1 + 2*15 = 150
    cheap = build_edit_plan(IDENTITY, segs, 0, 100, 15.0)
    assert cheap.total_cost == pytest.approx(150.0)
    assert [i.id for i in cheap.items] == ["a1", "b", "a2"]
    # 高切换成本：全程 b = 200
    pricy = build_edit_plan(IDENTITY, segs, 0, 100, 100.0)
    assert pricy.total_cost == pytest.approx(200.0)
    assert [i.id for i in pricy.items] == ["b"]


def test_no_switch_cost_when_id_unchanged_across_intervals():
    # 区间 [0,50) 覆盖 {a,b}，[50,100) 覆盖 {a,c}；a 全程 cost 2，b/c cost 1。
    # 选 a 贯穿两个区间：同 id 无切换费 = 200；b->c 需要一次切换 = 100 + s。
    segs = [
        _seg("a", "A", 0, 100, 2.0),
        _seg("b", "B", 0, 50, 1.0),
        _seg("c", "B", 50, 100, 1.0),
    ]
    res = build_edit_plan(IDENTITY, segs, 0, 100, 200.0)
    assert res.total_cost == pytest.approx(200.0)
    assert [i.id for i in res.items] == ["a"]
    assert len(res.items) == 1


def test_staying_with_expensive_beats_two_switches():
    # x1 覆盖 0-40（cost 2），x2 覆盖 60-100（cost 2），中间 40-60 只能 z（cost 2）；
    # y 全程 cost 3。
    segs = [
        _seg("x1", "A", 0, 40, 2.0),
        _seg("x2", "A", 60, 100, 2.0),
        _seg("z", "A", 40, 60, 2.0),
        _seg("y", "B", 0, 100, 3.0),
    ]
    # 拼接方案 x1,z,x2：200 + 两次切换；全程 y：300
    switching = build_edit_plan(IDENTITY, segs, 0, 100, 10.0)
    assert switching.total_cost == pytest.approx(220.0)
    assert [i.id for i in switching.items] == ["x1", "z", "x2"]
    all_y = build_edit_plan(IDENTITY, segs, 0, 100, 60.0)
    assert all_y.total_cost == pytest.approx(300.0)
    assert [i.id for i in all_y.items] == ["y"]


def test_lexicographic_tie_break_on_full_sequence():
    # 单区间内同价 id：取字典序最小
    segs = [
        _seg("b", "B", 0, 100, 1.0),
        _seg("a", "A", 0, 100, 1.0),
    ]
    res = build_edit_plan(IDENTITY, segs, 0, 100, 0.0)
    assert [i.id for i in res.items] == ["a"]

    # 跨三个小区间的序列级同价：
    #   [0,10):   {a cost 1.2, b cost 0.2}
    #   [10,90):  {a cost 1.2, c cost 1.2}
    #   [90,100): {a cost 1.2, d cost 0.2}
    # 全程 a：100*1.2 = 120，无切换；
    # b,c,d：10*0.2 + 80*1.2 + 10*0.2 = 100，另加 2 次切换。
    # switch=10 时两条路径同为 120，逐区间 id 序列 [a,a,a] 字典序更小。
    segs2 = [
        _seg("a", "A", 0, 100, 1.2),
        _seg("b", "B", 0, 10, 0.2),
        _seg("c", "B", 10, 90, 1.2),
        _seg("d", "B", 90, 100, 0.2),
    ]
    tied = build_edit_plan(IDENTITY, segs2, 0, 100, 10.0)
    assert tied.total_cost == pytest.approx(120.0)
    assert [i.id for i in tied.items] == ["a"]
    # switch=9 时拼接更便宜：首尾用 b/d；中间 a 与 c 同价，但作为次区间
    # 选择 [b,a,d] 比 [b,c,d] 字典序更小（两者切换次数相同）。
    cheaper = build_edit_plan(IDENTITY, segs2, 0, 100, 9.0)
    assert cheaper.total_cost == pytest.approx(118.0)
    assert [i.id for i in cheaper.items] == ["b", "a", "d"]


def test_merged_plan_items_time_sorted_and_same_id_adjacent_merged():
    # 三个小区间依次选 x / x / y
    segs = [
        _seg("x", "A", 0, 80, 1.0),
        _seg("y", "B", 20, 100, 1.0),
    ]
    # 区间 0-20 {x}，20-80 {x,y}，80-100 {y} -> x,x,y，合并后两项
    res = build_edit_plan(IDENTITY, segs, 0, 100, 0.0)
    assert [i.id for i in res.items] == ["x", "y"]
    assert (res.items[0].ref_start_ms, res.items[0].ref_end_ms) == (0.0, 80.0)
    assert (res.items[1].ref_start_ms, res.items[1].ref_end_ms) == (80.0, 100.0)
    starts = [i.ref_start_ms for i in res.items]
    assert starts == sorted(starts)


def test_gaps_returned_and_merged():
    segs = [_seg("a", "A", 0, 20, 1.0), _seg("b", "B", 40, 60, 1.0)]
    res = build_edit_plan(IDENTITY, segs, 0, 100, 1.0)
    assert isinstance(res, list)
    assert all(isinstance(g, Gap) for g in res)
    assert [(g.start_ms, g.end_ms) for g in res] == [(20.0, 40.0), (60.0, 100.0)]


def test_no_segment_touches_window_whole_window_is_one_gap():
    res = build_edit_plan(IDENTITY, [_seg("x", "A", 200, 300, 1.0)], 0, 100, 1.0)
    assert [(g.start_ms, g.end_ms) for g in res] == [(0.0, 100.0)]


def test_segment_must_fully_cover_interval_partial_overlap_is_gap():
    # 片段端点参与划分：x=[0,50) 完整覆盖 [0,50)，[50,100) 无覆盖 -> 空档
    res = build_edit_plan(IDENTITY, [_seg("x", "A", 0, 50, 1.0)], 0, 100, 1.0)
    assert [(g.start_ms, g.end_ms) for g in res] == [(50.0, 100.0)]
    # 但把窗口收进片段内则可行
    ok = build_edit_plan(IDENTITY, [_seg("x", "A", 0, 50, 1.0)], 10, 40, 1.0)
    assert ok.total_cost == pytest.approx(30.0)


def test_segments_clipped_to_window():
    segs = [_seg("x", "A", -50, 150, 2.0)]
    res = build_edit_plan(IDENTITY, segs, 0, 100, 3.0)
    assert res.total_cost == pytest.approx(200.0)
    assert len(res.items) == 1
    assert (res.items[0].ref_start_ms, res.items[0].ref_end_ms) == (0.0, 100.0)


def test_inverse_transform_uses_device_mapping():
    # B：u = 2*t + 10
    mappings = {"B": (2.0, 10.0)}
    res = build_edit_plan(
        mappings, [_seg("s", "B", 0, 200, 1.0)], 20.0, 100.0, 0.0
    )
    item = res.items[0]
    assert (item.ref_start_ms, item.ref_end_ms) == (20.0, 100.0)
    assert item.local_start_ms == pytest.approx(5.0)
    assert item.local_end_ms == pytest.approx(45.0)
    # 时长按参考时间计（80ms），而非本地时长（40ms）
    assert res.total_cost == pytest.approx(80.0)


def test_zero_cost_segments_and_switch_cost_zero():
    segs = [_seg("z", "A", 0, 100, 0.0)]
    res = build_edit_plan(IDENTITY, segs, 0, 100, 0.0)
    assert res.total_cost == 0.0


def test_zero_length_and_touch_only_segments_dropped():
    # 与窗口仅端点相接（在 100 处）或零长度：裁剪后为空
    segs = [
        _seg("touch", "A", 100, 200, 1.0),
        _seg("point", "B", 50, 50, 1.0),
    ]
    res = build_edit_plan(IDENTITY, segs, 0, 100, 1.0)
    assert [(g.start_ms, g.end_ms) for g in res] == [(0.0, 100.0)]
