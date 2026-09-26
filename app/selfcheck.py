"""容器启动前的初始化自检：确认 NumPy 最小二乘与编辑计划求解器可用。"""

from __future__ import annotations

import numpy as np

from app.edit_plan import PlanSegment, build_edit_plan


def _check_least_squares() -> None:
    a_mat = np.array([[1.0, 1.0], [2.0, 1.0], [4.0, 1.0]])
    b_vec = np.array([2.0, 3.0, 5.0])
    solution, _, rank, _ = np.linalg.lstsq(a_mat, b_vec, rcond=None)
    if rank != 2 or not np.allclose(solution, [1.0, 1.0]):
        raise SystemExit("[selfcheck] NumPy 最小二乘自检失败")
    print(f"[selfcheck] numpy {np.__version__} 最小二乘自检通过")


def _check_edit_plan() -> None:
    # 窗口 [0, 150)：a 覆盖 [0,100)，b 覆盖 [50,150)，高切换成本下
    # 最优为 a 用满后切一次 b：50*1 + 50*1 + 50*2 + 500 = 700
    segments = [
        PlanSegment("a", "dev", 1.0, 0.0, 0.0, 100.0, 1.0),
        PlanSegment("b", "dev", 1.0, 0.0, 50.0, 150.0, 2.0),
    ]
    plan = build_edit_plan(segments, 0.0, 150.0, 500.0)
    if [item.seg_id for item in plan.items] != ["a", "b"] or plan.total_cost != 700.0:
        raise SystemExit("[selfcheck] 编辑计划求解器自检失败")
    print("[selfcheck] 编辑计划求解器自检通过")


def main() -> None:
    _check_least_squares()
    _check_edit_plan()


if __name__ == "__main__":
    main()
