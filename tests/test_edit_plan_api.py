"""POST /v1/edit-plan 的接口行为测试。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

# 同步点使 B 的精确映射为 unified = local - 50（逆变换 local = unified + 50）
SYNC_POINTS = [
    {"device_a": "A", "time_a_ms": 0.0, "device_b": "B", "time_b_ms": 50.0},
    {"device_a": "A", "time_a_ms": 1000.0, "device_b": "B", "time_b_ms": 1050.0},
    {"device_a": "A", "time_a_ms": 2000.0, "device_b": "B", "time_b_ms": 2050.0},
]


def _payload(**overrides):
    body = {
        "devices": ["A", "B"],
        "reference_device": "A",
        "sync_points": SYNC_POINTS,
        "tolerance_ms": 1e-6,
        "window_start_ms": 0.0,
        "window_end_ms": 1000.0,
        "switch_cost": 0.0,
        "segments": [
            # B 本地 [50,1050) -> 参考 [0,1000)，全程可覆盖
            {"id": "b-long", "device": "B", "start_ms": 50.0, "end_ms": 1050.0, "cost_per_ms": 2.0},
            # A 本地 [200,400)，更便宜的中段
            {"id": "a-part", "device": "A", "start_ms": 200.0, "end_ms": 400.0, "cost_per_ms": 1.0},
        ],
    }
    body.update(overrides)
    return body


def test_success_cheapest_plan_with_inverse_local_times():
    # switch=0：中段用 a-part（1/ms），其余 b-long（2/ms）
    r = client.post("/v1/edit-plan", json=_payload())
    assert r.status_code == 200, r.text
    data = r.json()
    # 200*2 + 200*1 + 600*2 = 1800
    assert data["total_cost"] == pytest.approx(1800.0)
    plan = data["plan"]
    assert [p["id"] for p in plan] == ["b-long", "a-part", "b-long"]
    # 按参考时间排序
    assert [p["ref_start_ms"] for p in plan] == sorted(p["ref_start_ms"] for p in plan)

    first = plan[0]
    assert first["device"] == "B"
    assert (first["ref_start_ms"], first["ref_end_ms"]) == (0.0, 200.0)
    # 逆变换 local = ref + 50（经仿射反算，允许浮点误差）
    assert first["local_start_ms"] == pytest.approx(50.0)
    assert first["local_end_ms"] == pytest.approx(250.0)

    mid = plan[1]
    assert mid["device"] == "A"
    assert (mid["ref_start_ms"], mid["ref_end_ms"]) == (200.0, 400.0)
    assert mid["local_start_ms"] == pytest.approx(200.0)
    assert mid["local_end_ms"] == pytest.approx(400.0)

    last = plan[2]
    assert (last["ref_start_ms"], last["ref_end_ms"]) == (400.0, 1000.0)
    assert last["local_start_ms"] == pytest.approx(450.0)
    assert last["local_end_ms"] == pytest.approx(1050.0)


def test_high_switch_cost_keeps_single_segment_and_merges():
    # switch=1000：两次切换共 2000，远高于中段省下的 200 -> 全程 b-long
    r = client.post("/v1/edit-plan", json=_payload(switch_cost=1000.0))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_cost"] == pytest.approx(2000.0)
    plan = data["plan"]
    assert [p["id"] for p in plan] == ["b-long"]
    # 相邻同 id 区间合并成一整段
    assert (plan[0]["ref_start_ms"], plan[0]["ref_end_ms"]) == (0.0, 1000.0)
    assert plan[0]["local_start_ms"] == pytest.approx(50.0)
    assert plan[0]["local_end_ms"] == pytest.approx(1050.0)


def test_segments_clipped_to_window():
    # b-long 换算后超出窗口，结果仍被裁到窗口边界
    r = client.post(
        "/v1/edit-plan",
        json=_payload(segments=[
            {"id": "b-long", "device": "B", "start_ms": -950.0, "end_ms": 2050.0, "cost_per_ms": 1.0},
        ]),
    )
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert len(plan) == 1
    assert (plan[0]["ref_start_ms"], plan[0]["ref_end_ms"]) == (0.0, 1000.0)


def test_gaps_return_422_with_merged_ranges():
    r = client.post(
        "/v1/edit-plan",
        json=_payload(segments=[
            {"id": "a-part", "device": "A", "start_ms": 200.0, "end_ms": 400.0, "cost_per_ms": 1.0},
        ]),
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "uncovered_gaps"
    assert detail["gaps"] == [
        {"start_ms": 0.0, "end_ms": 200.0},
        {"start_ms": 400.0, "end_ms": 1000.0},
    ]


def test_calibration_failure_reuses_original_error():
    # 残差超容限
    r = client.post(
        "/v1/edit-plan",
        json=_payload(
            sync_points=[
                {"device_a": "A", "time_a_ms": 0.0, "device_b": "B", "time_b_ms": 0.0},
                {"device_a": "A", "time_a_ms": 1000.0, "device_b": "B", "time_b_ms": 1000.0},
                {"device_a": "A", "time_a_ms": 2000.0, "device_b": "B", "time_b_ms": 2050.0},
            ],
            tolerance_ms=5.0,
        ),
    )
    assert r.status_code == 422
    assert "超过容限" in r.json()["detail"]

    # 不满列秩
    r2 = client.post(
        "/v1/edit-plan",
        json=_payload(
            devices=["A", "B", "C"],
            sync_points=SYNC_POINTS,
            tolerance_ms=1e-6,
        ),
    )
    assert r2.status_code == 422
    assert "不满列秩" in r2.json()["detail"]


def test_unknown_and_reversed_segment_rejected():
    r = client.post(
        "/v1/edit-plan",
        json=_payload(segments=[
            {"id": "x", "device": "Z", "start_ms": 0.0, "end_ms": 1.0, "cost_per_ms": 1.0},
        ]),
    )
    assert r.status_code == 422
    assert "未知设备" in r.json()["detail"]

    r2 = client.post(
        "/v1/edit-plan",
        json=_payload(segments=[
            {"id": "x", "device": "A", "start_ms": 9.0, "end_ms": 1.0, "cost_per_ms": 1.0},
        ]),
    )
    assert r2.status_code == 422
    assert "起止颠倒" in r2.json()["detail"]


def test_cost_and_window_validation():
    # 负成本
    r = client.post(
        "/v1/edit-plan",
        json=_payload(segments=[
            {"id": "x", "device": "A", "start_ms": 0.0, "end_ms": 1.0, "cost_per_ms": -0.5},
        ]),
    )
    assert r.status_code == 422

    # 负切换成本
    assert client.post("/v1/edit-plan", json=_payload(switch_cost=-1.0)).status_code == 422

    # 窗口起点等于终点
    assert client.post(
        "/v1/edit-plan", json=_payload(window_start_ms=10.0, window_end_ms=10.0)
    ).status_code == 422
    # 窗口起点大于终点
    assert client.post(
        "/v1/edit-plan", json=_payload(window_start_ms=11.0, window_end_ms=10.0)
    ).status_code == 422

    # NaN / 无穷成本（TestClient 默认 JSON 编码器拒绝 NaN，故手动构造 body）
    headers = {"Content-Type": "application/json"}
    nan_body = json.dumps(_payload(segments=[
        {"id": "x", "device": "A", "start_ms": 0.0, "end_ms": 1.0, "cost_per_ms": float("nan")},
    ]))
    assert client.post("/v1/edit-plan", content=nan_body, headers=headers).status_code == 422
    inf_body = json.dumps(_payload(segments=[
        {"id": "x", "device": "A", "start_ms": 0.0, "end_ms": 1.0, "cost_per_ms": float("inf")},
    ]))
    assert client.post("/v1/edit-plan", content=inf_body, headers=headers).status_code == 422


def test_duplicate_segment_ids_rejected():
    r = client.post(
        "/v1/edit-plan",
        json=_payload(segments=[
            {"id": "dup", "device": "A", "start_ms": 0.0, "end_ms": 100.0, "cost_per_ms": 1.0},
            {"id": "dup", "device": "A", "start_ms": 200.0, "end_ms": 300.0, "cost_per_ms": 1.0},
        ]),
    )
    assert r.status_code == 422
    assert "id" in r.text


def test_lexicographic_tie_break_via_api():
    # 两个全程片段同价 -> 字典序小的 id
    r = client.post(
        "/v1/edit-plan",
        json=_payload(
            switch_cost=0.0,
            segments=[
                {"id": "zeta", "device": "A", "start_ms": 0.0, "end_ms": 1000.0, "cost_per_ms": 1.0},
                {"id": "alpha", "device": "A", "start_ms": 0.0, "end_ms": 1000.0, "cost_per_ms": 1.0},
            ],
        ),
    )
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert [p["id"] for p in plan] == ["alpha"]


def test_calibrate_endpoint_still_available():
    # 原标定接口保持不变
    r = client.post(
        "/v1/calibrate",
        json={
            "devices": ["A", "B"],
            "reference_device": "A",
            "sync_points": SYNC_POINTS,
            "tolerance_ms": 1e-6,
            "segments": [
                {"device": "B", "start_ms": 1050.0, "end_ms": 2050.0, "label": "b"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["mappings"]["B"] == pytest.approx({"slope": 1.0, "intercept_ms": -50.0})
    assert data["segments"][0]["start_ms"] == pytest.approx(1000.0)
    assert data["segments"][0]["end_ms"] == pytest.approx(2000.0)
