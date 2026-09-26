# 多录音机时钟联合标定与剪辑规划服务

面向离线采访素材的 Python FastAPI 服务：用 NumPy 最小二乘联立全部同步点，
把多台录音机的本地毫秒时钟统一换算到参考设备时间轴；并可在同一标定结果上，
按参考时间窗求解全局最小成本的剪辑计划。**服务只处理时间数值，不读写任何音频文件。**

- 参考设备的映射固定为原时间（斜率 1、截距 0）；
- 其余每台设备采用正斜率仿射映射 `unified_ms = slope * device_ms + intercept_ms`；
- 出现以下任一情况即拒绝并说明原因（HTTP 422）：
  - 同步点方程组不满列秩（无法唯一确定所有设备映射）；
  - 某设备拟合斜率非正；
  - 任一同步点残差超过非负容限；
  - 请求引用未知设备，或片段起止时间颠倒；
  - `edit-plan` 的成本/切换成本为负或非有限值、时间窗起点不小于终点、片段 id 重复；
  - `edit-plan` 的参考时间窗内存在无任何片段完整覆盖的空档（返回全部合并后的空档范围）。

## 启动

单条命令（依赖安装与初始化自检均已纳入镜像构建和容器启动，无需额外步骤）：

```bash
docker compose up --build
```

容器启动时会先运行初始化自检（验证 NumPy 最小二乘可用），自检通过后自动拉起 uvicorn。

## 配置

| 环境变量   | 默认值 | 说明                                         |
| ---------- | ------ | -------------------------------------------- |
| `API_PORT` | `8000` | 宿主机暴露端口（容器内固定监听 8000）       |

自定义端口示例：`API_PORT=9000 docker compose up --build`

## API 地址

默认配置（`API_PORT=8000`）下：

- 服务地址：`http://localhost:8000`（若修改 `API_PORT` 则相应变化）
- 交互式文档：`http://localhost:8000/docs`
- 健康检查：`GET /health`
- 标定接口：`POST /v1/calibrate`
- 剪辑规划接口：`POST /v1/edit-plan`

---

## 1. 标定接口 `POST /v1/calibrate`

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

---

## 2. 剪辑规划接口 `POST /v1/edit-plan`

在与标定接口完全相同的标定参数（设备、参考设备、同步点、容限）与最小二乘求解器之上，
对候选片段做成本最优剪辑规划：

1. 标定成功后，把每个片段的本地起止换算到统一（参考）时间，并裁剪到参考时间窗
   `[window_start_ms, window_end_ms)`（半开区间）；
2. 以所有片段端点和窗口端点排序去重，划分半开小区间；
3. 每个小区间必须由一个**完整覆盖**它（片段起点 ≤ 区间起点且片段终点 ≥ 区间终点）
   的片段承担；没有任何片段覆盖的连续小区间合并为空档；
4. 全部区间均有覆盖时，用动态规划全局最小化：

   ```
   Σ (小区间时长_ms × 所选片段 cost_per_ms)
     + 相邻小区间所选片段 id 变化次数 × switch_cost
   ```

   总成本相同时，选择**逐区间 id 序列字典序最小**的方案；
5. 成功计划按参考时间排序，相邻且 id 相同的项合并为一条；每条同时给出参考起止
   和经逆变换 `device_ms = (unified_ms - intercept_ms) / slope` 得到的设备本地起止。

### 请求示例

```bash
curl -X POST http://localhost:8000/v1/edit-plan \
  -H 'Content-Type: application/json' \
  -d '{
    "devices": ["recorder_a", "recorder_b"],
    "reference_device": "recorder_a",
    "sync_points": [
      {"device_a": "recorder_a", "time_a_ms": 0,    "device_b": "recorder_b", "time_b_ms": 50},
      {"device_a": "recorder_a", "time_a_ms": 1000, "device_b": "recorder_b", "time_b_ms": 1050},
      {"device_a": "recorder_a", "time_a_ms": 2000, "device_b": "recorder_b", "time_b_ms": 2050}
    ],
    "tolerance_ms": 1.0,
    "window_start_ms": 0,
    "window_end_ms": 10000,
    "switch_cost": 500,
    "segments": [
      {"id": "seg-b-full", "device": "recorder_b", "start_ms": 50,    "end_ms": 10050, "cost_per_ms": 2.0},
      {"id": "seg-a-mid",  "device": "recorder_a", "start_ms": 3000,  "end_ms": 6000,  "cost_per_ms": 1.0}
    ]
  }'
```

字段说明（标定部分与 `/v1/calibrate` 相同，标定失败沿用同一错误）：

- `segments[].id`：片段**唯一**字符串标识（不可重复）；
- `segments[].device` / `start_ms` / `end_ms`：片段所属设备与本地起止，
  `start_ms` 不得大于 `end_ms`；
- `segments[].cost_per_ms`：采用该片段时每参考毫秒的成本，**必须有限且非负**；
- `window_start_ms` / `window_end_ms`：参考时间窗（半开），
  **起点必须严格小于终点**；
- `switch_cost`：相邻小区间更换片段 id 一次的成本，必须有限且非负。

### 成功响应（200）

```json
{
  "total_cost": 15500.0,
  "plan": [
    {"id": "seg-b-full", "device": "recorder_b", "ref_start_ms": 0.0,    "ref_end_ms": 3000.0, "local_start_ms": 50.0,    "local_end_ms": 3050.0},
    {"id": "seg-a-mid",  "device": "recorder_a", "ref_start_ms": 3000.0, "ref_end_ms": 6000.0, "local_start_ms": 3000.0,  "local_end_ms": 6000.0},
    {"id": "seg-b-full", "device": "recorder_b", "ref_start_ms": 6000.0, "ref_end_ms": 10000.0, "local_start_ms": 6050.0, "local_end_ms": 10050.0}
  ]
}
```

成本核算：`3000×2 + 3000×1 + 4000×2 + 2×500 = 15500`（两次换片）。
`total_cost` 为全局最小成本；`plan` 已按参考时间排序，相邻同 id 的区间已合并。

### 空档响应（422）

当窗口内存在无任何片段完整覆盖的时间范围时返回 422，`gaps` 为**全部合并后**、
按起点升序且互不重叠的空档（参考时间毫秒）：

```json
{
  "detail": {
    "error": "uncovered_gaps",
    "message": "参考时间窗内存在无任何片段完整覆盖的空档",
    "gaps": [
      {"start_ms": 0.0, "end_ms": 3000.0},
      {"start_ms": 6000.0, "end_ms": 10000.0}
    ]
  }
}
```

### 标定失败与参数校验（422）

标定失败时返回与 `/v1/calibrate` 完全相同的错误文案，例如：

```json
{"detail": "第 2 个同步点残差 50.000 ms 超过容限 1.0 ms，拒绝标定"}
```

## 本地开发

```bash
pip install -r requirements.txt
python -m pytest tests/        # 运行测试
uvicorn app.main:app --reload  # 本地起服务（默认 http://localhost:8000）
```

## 项目结构

```
app/
  main.py         # FastAPI 入口、请求校验与两个接口
  calibration.py  # NumPy 联合最小二乘标定核心（两接口共用）
  planner.py      # 片段裁剪、区间划分、空档合并与最小成本动态规划
  models.py       # 请求/响应模型
  selfcheck.py    # 容器启动时的初始化自检
tests/            # 标定核心、规划核心与接口行为测试
Dockerfile        # 依赖安装 + 启动（自检后拉起 uvicorn）
docker-compose.yml
```
