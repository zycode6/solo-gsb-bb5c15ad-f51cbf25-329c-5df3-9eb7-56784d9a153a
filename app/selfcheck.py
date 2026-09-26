"""容器启动前的初始化自检：确认 NumPy 最小二乘可用。"""

from __future__ import annotations

import numpy as np


def main() -> None:
    a_mat = np.array([[1.0, 1.0], [2.0, 1.0], [4.0, 1.0]])
    b_vec = np.array([2.0, 3.0, 5.0])
    solution, _, rank, _ = np.linalg.lstsq(a_mat, b_vec, rcond=None)
    if rank != 2 or not np.allclose(solution, [1.0, 1.0]):
        raise SystemExit("[selfcheck] NumPy 最小二乘自检失败")
    print(f"[selfcheck] numpy {np.__version__} 最小二乘自检通过")


if __name__ == "__main__":
    main()
