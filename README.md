# 多录音机时钟联合标定服务

面向离线采访素材的 Python FastAPI 服务：用 NumPy 最小二乘联立全部同步点，
把多台录音机的本地毫秒时钟统一换算到参考设备时间轴；并可在统一时间轴上
为指定参考时间窗求解总成本最低的片段编辑计划（`POST /v1/edit-plan`）。

- 参考设备的映射固定为原时间（斜率 1、截距 0）；
- 其余每台设备采用正斜率仿射映射 `unified_ms = slope * device_ms + intercept_ms`；
- 出现以下任一情况即拒绝并说明原因（HTTP 422）：
  - 同步点方程组不满列秩（无法唯一确定所有设备映射）；
  - 某设备拟合斜率非正；
  - 任一同步点残差超过非负容限；
  - 请求引用未知设备，或片段起止时间颠倒。

## 启动

单条命令（依赖安装与初始化自检均已纳入镜像构建和容器启动）：

```bash
docker compose up --build
```

## 配置

| 环境变量   | 默认值 | 说明                     |
| ---------- | ------ | ------------------------ |
| `API_PORT` | `8000` | 宿主机暴露端口           |

## API 地址

- 服务地址：`http://localhost:8000`（若修改 `API_PORT` 则相应变化）
- 交互式文档：`http://localhost:8000/docs`
- 健康检查：`GET /health`
- 标定接口：`POST /v1/calibrate`
- 编辑计划接口：`POST /v1/edit-plan`

### 请求示例

```bash
curl -X POST http://localhost:8000/v1/calibrate \
  -H 'Content-Type: application/json' \
  -d '{
    "devices": ["recorder_a", "recorder_b"],
    "reference_device": "recorder_a",
    "sync_points": [
      {"device_a": "recorder_a", "time_a_ms": 0,    "device_b": "recorder_b", "time_b_ms": 50},
      {"device_a": "recorder_a", "time_a_ms": 1000, "device_b": "recorder_b", "time_b_ms": 1051},
      {"device_a": "recorder_a", "time_a_ms": 2000, "device_b": "recorder_b", "time_b_ms": 2052}
    ],
    "tolerance_ms": 1.0,
    "segments": [
      {"device": "recorder_b", "start_ms": 5000, "end_ms": 6000, "label": "b_part"},
      {"device": "recorder_a", "start_ms": 100,  "end_ms": 900,  "label": "a_part"}
    ]
  }'
```

字段说明：

- `devices`：全部录音机设备名（不可重复）；
- `reference_device`：参考设备，必须在 `devices` 中；
- `sync_points`：成对同步点，每条为同一物理时刻在两台设备上各自的毫秒读数；
- `tolerance_ms`：非负残差容限（毫秒）；
- `segments`：片段起止（设备本地毫秒），`start_ms` 不得大于 `end_ms`。

### 成功响应（200）

```json
{
  "reference_device": "recorder_a",
  "mappings": {
    "recorder_a": {"slope": 1.0, "intercept_ms": 0.0},
    "recorder_b": {"slope": 0.999000999000999, "intercept_ms": -49.95004995005066}
  },
  "segments": [
    {"device": "recorder_a", "label": "a_part", "start_ms": 100.0, "end_ms": 900.0},
    {"device": "recorder_b", "label": "b_part", "start_ms": 4945.054945054944, "end_ms": 5944.055944055944}
  ],
  "sync_point_residuals_ms": [7.1e-13, 6.8e-13, 6.8e-13],
  "max_residual_ms": 7.1e-13
}
```

`mappings` 为各设备映射系数；`segments` 为统一时间下的片段起止，已按起点升序排列。

### 失败响应（422）

```json
{"detail": "同步点方程组不满列秩（秩 2 < 未知数 4），无法唯一确定所有设备的映射，请补充与参考设备连通的同步点"}
```

## 编辑计划接口 `POST /v1/edit-plan`

复用与 `/v1/calibrate` 相同的标定参数与求解器。标定成功后，把各片段换算到
统一时间轴并裁剪到请求给定的参考时间窗；用全部片段端点与窗口端点把窗口
划分为半开小区间，每个小区间选择一个完整覆盖它的片段，全局最小化：

```
总成本 = Σ(区间时长 × 所选片段 cost_per_ms) + 相邻区间更换片段 id 的次数 × switch_cost
```

同价时取逐区间 id 序列字典序最小者。服务只输出计划，不处理音频文件。

### 请求示例

```bash
curl -X POST http://localhost:8000/v1/edit-plan \
  -H 'Content-Type: application/json' \
  -d '{
    "devices": ["A", "B"],
    "reference_device": "A",
    "sync_points": [
      {"device_a": "A", "time_a_ms": 0,    "device_b": "B", "time_b_ms": 100},
      {"device_a": "A", "time_a_ms": 1000, "device_b": "B", "time_b_ms": 1100}
    ],
    "tolerance_ms": 0.5,
    "segments": [
      {"id": "a1", "device": "A", "start_ms": 0,   "end_ms": 1000, "cost_per_ms": 1.0},
      {"id": "b1", "device": "B", "start_ms": 600, "end_ms": 1600, "cost_per_ms": 0.5}
    ],
    "window_start_ms": 0,
    "window_end_ms": 1500,
    "switch_cost": 10.0
  }'
```

字段说明（标定相关字段与 `/v1/calibrate` 一致）：

- `segments[].id`：片段唯一标识字符串，请求内不可重复；
- `segments[].cost_per_ms`：单位毫秒成本，有限且非负；
- `window_start_ms` / `window_end_ms`：参考时间窗（统一时间），起点必须小于终点；
- `switch_cost`：相邻小区间更换片段 id 的单次成本，有限且非负。

### 成功响应（200）

```json
{
  "reference_device": "A",
  "window_start_ms": 0.0,
  "window_end_ms": 1500.0,
  "total_cost": 1010.0,
  "plan": [
    {"id": "a1", "device": "A", "start_ms": 0.0,   "end_ms": 500.0,  "device_start_ms": 0.0,   "device_end_ms": 500.0},
    {"id": "b1", "device": "B", "start_ms": 500.0, "end_ms": 1500.0, "device_start_ms": 600.0, "device_end_ms": 1600.0}
  ]
}
```

`plan` 按时间升序，同一片段的相邻项已合并；每项含片段 `id`、参考时间起止
（`start_ms` / `end_ms`）与逆变换后的设备本地起止（`device_start_ms` /
`device_end_ms`）。`total_cost` 为总成本。

### 空档响应（422）

窗口内存在没有任何片段完整覆盖的空档时，返回 422 及全部合并后的空档范围
（参考时间）：

```json
{
  "detail": {
    "message": "窗口内存在无片段覆盖的空档：[200.0, 400.0)",
    "gaps_ms": [{"start_ms": 200.0, "end_ms": 400.0}]
  }
}
```

标定失败（秩亏、斜率非正、残差超容限等）时沿用 `/v1/calibrate` 的 422 错误格式。

## 本地开发

```bash
pip install -r requirements.txt
python -m pytest tests/        # 运行测试
uvicorn app.main:app --reload  # 本地起服务
```

## 项目结构

```
app/
  main.py         # FastAPI 入口与请求校验（/v1/calibrate、/v1/edit-plan）
  calibration.py  # NumPy 联合最小二乘标定核心
  edit_plan.py    # 编辑计划优化：区间划分、空档检测、动态规划求解
  models.py       # 请求/响应模型
  selfcheck.py    # 容器启动时的初始化自检（最小二乘 + 编辑计划求解器）
tests/            # 行为测试
Dockerfile        # 依赖安装 + 启动（自检后拉起 uvicorn）
docker-compose.yml
```
