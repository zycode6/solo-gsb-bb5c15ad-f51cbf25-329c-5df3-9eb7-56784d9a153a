"""标定核心与 API 的行为测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.calibration import CalibrationError, SyncPoint, calibrate
from app.main import app

client = TestClient(app)


# ---------- 核心标定逻辑 ----------

def test_two_devices_exact_fit():
    # B 的时钟偏快：A 每走 1000ms，B 走 1001ms，且 B 读数整体超前 50ms
    sps = [
        SyncPoint("A", 0.0, "B", 50.0),
        SyncPoint("A", 1000.0, "B", 1051.0),
        SyncPoint("A", 2000.0, "B", 2052.0),
    ]
    res = calibrate(["A", "B"], "A", sps, tolerance_ms=1.0)
    slope, intercept = res.mappings["B"]
    assert slope == pytest.approx(1000.0 / 1001.0)
    assert intercept == pytest.approx(-50.0 * 1000.0 / 1001.0)
    assert res.mappings["A"] == (1.0, 0.0)
    assert max(res.residuals_ms) <= 1.0


def test_joint_fit_with_cross_pairs_between_non_reference_devices():
    # C 与 A 无直接同步点，但通过 B-C 对连通
    sps = [
        SyncPoint("A", 0.0, "B", 10.0),
        SyncPoint("A", 1000.0, "B", 1010.0),
        SyncPoint("B", 10.0, "C", 20.0),
        SyncPoint("B", 1010.0, "C", 1020.0),
    ]
    res = calibrate(["A", "B", "C"], "A", sps, tolerance_ms=1e-6)
    assert res.mappings["B"] == pytest.approx((1.0, -10.0))
    assert res.mappings["C"] == pytest.approx((1.0, -20.0))


def test_rank_deficient_system_rejected():
    # C 没有任何同步点约束 -> 秩 2 < 未知数 4
    sps = [
        SyncPoint("A", 0.0, "B", 10.0),
        SyncPoint("A", 1000.0, "B", 1010.0),
    ]
    with pytest.raises(CalibrationError, match="不满列秩"):
        calibrate(["A", "B", "C"], "A", sps, tolerance_ms=10.0)


def test_non_positive_slope_rejected():
    # B 的读数相对 A 倒退 -> 斜率 -1
    sps = [
        SyncPoint("A", 0.0, "B", 1000.0),
        SyncPoint("A", 1000.0, "B", 0.0),
    ]
    with pytest.raises(CalibrationError, match="斜率非正"):
        calibrate(["A", "B"], "A", sps, tolerance_ms=1000.0)


def test_residual_over_tolerance_rejected():
    sps = [
        SyncPoint("A", 0.0, "B", 0.0),
        SyncPoint("A", 1000.0, "B", 1000.0),
        SyncPoint("A", 2000.0, "B", 2050.0),
    ]
    with pytest.raises(CalibrationError, match="超过容限"):
        calibrate(["A", "B"], "A", sps, tolerance_ms=5.0)
    # 放宽容限则通过
    res = calibrate(["A", "B"], "A", sps, tolerance_ms=50.0)
    assert max(res.residuals_ms) <= 50.0


def test_unknown_device_and_reference_errors():
    with pytest.raises(CalibrationError, match="未知设备"):
        calibrate(["A", "B"], "A", [SyncPoint("A", 0.0, "X", 0.0)], 1.0)
    with pytest.raises(CalibrationError, match="参考设备"):
        calibrate(["A", "B"], "ZZ", [], 1.0)


def test_single_reference_device_needs_no_sync_points():
    res = calibrate(["A"], "A", [], tolerance_ms=0.0)
    assert res.mappings == {"A": (1.0, 0.0)}


# ---------- API 行为 ----------

def _payload(**overrides):
    body = {
        "devices": ["A", "B"],
        "reference_device": "A",
        "sync_points": [
            {"device_a": "A", "time_a_ms": 0.0, "device_b": "B", "time_b_ms": 50.0},
            {"device_a": "A", "time_a_ms": 1000.0, "device_b": "B", "time_b_ms": 1051.0},
            {"device_a": "A", "time_a_ms": 2000.0, "device_b": "B", "time_b_ms": 2052.0},
        ],
        "tolerance_ms": 1.0,
        "segments": [
            {"device": "B", "start_ms": 5000.0, "end_ms": 6000.0, "label": "b_seg"},
            {"device": "A", "start_ms": 100.0, "end_ms": 200.0, "label": "a_seg"},
        ],
    }
    body.update(overrides)
    return body


def test_api_success_returns_mappings_and_sorted_segments():
    r = client.post("/v1/calibrate", json=_payload())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["mappings"]["A"] == {"slope": 1.0, "intercept_ms": 0.0}
    assert data["mappings"]["B"]["slope"] == pytest.approx(1000.0 / 1001.0)
    starts = [s["start_ms"] for s in data["segments"]]
    assert starts == sorted(starts)
    # A 的片段按原时间排在最前
    assert data["segments"][0]["label"] == "a_seg"
    assert data["segments"][0]["start_ms"] == pytest.approx(100.0)
    # B 的片段换算到统一时间：(1000/1001) * 5000 - 50 * (1000/1001)
    assert data["segments"][1]["start_ms"] == pytest.approx(4950.0 * 1000.0 / 1001.0)
    assert data["max_residual_ms"] <= 1.0


def test_api_rejects_unknown_segment_device():
    r = client.post(
        "/v1/calibrate",
        json=_payload(segments=[{"device": "X", "start_ms": 0.0, "end_ms": 1.0}]),
    )
    assert r.status_code == 422
    assert "未知设备" in r.json()["detail"]


def test_api_rejects_reversed_segment():
    r = client.post(
        "/v1/calibrate",
        json=_payload(segments=[{"device": "A", "start_ms": 10.0, "end_ms": 5.0}]),
    )
    assert r.status_code == 422
    assert "起止颠倒" in r.json()["detail"]


def test_api_rejects_negative_tolerance():
    r = client.post("/v1/calibrate", json=_payload(tolerance_ms=-0.1))
    assert r.status_code == 422


def test_api_rejects_calibration_failure_with_reason():
    r = client.post(
        "/v1/calibrate",
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


def test_health():
    assert client.get("/health").json() == {"status": "ok"}
