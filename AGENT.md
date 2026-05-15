# Tennis Highlight Extractor — 项目开发者指南

> **⚠️ 注意**：本文件与项目代码保持同步。当 `app/pipeline/`、`app/config.py`、`app/models.py`、测试文件、项目结构或规范发生变更时，AI 助手必须同步更新本文件。详见项目规则 `sync-agent-md`。

## 项目概述

从业余网球训练视频中自动**提取高光片段**的 Web 应用。用户上传固定三脚架拍摄的视频，系统分析运动并生成"比赛进行中"与"死球时间"的时间线，支持交互编辑和导出。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端框架 | FastAPI (Python 3.11+) |
| 视频处理 | OpenCV (`opencv-python-headless`), ffmpeg |
| ML 模型 | YOLOv5 (网球检测), YOLOv8 (球员检测), YOLOv8/YOLO11-pose (姿态检测), ResNet50 (场地关键点) |
| 数据库 | SQLite (`app/database.py` 管理) |
| 前端 | 纯 JS + HTML + CSS (单页应用, 无框架) |
| 测试 | `unittest` (Python) + `jsdom` (Node 前端测试) |
| 依赖管理 | `requirements.txt`（无 pyproject.toml） |

## 目录结构

```
tennis-highlight/
├── app/
│   ├── main.py                   # FastAPI 入口
│   ├── config.py                 # pydantic-settings 配置
│   ├── database.py               # SQLite CRUD
│   ├── models.py                 # Pydantic 请求/响应模型
│   ├── pipeline/                 # 核心处理管线
│   │   ├── ball_tracking.py      # YOLO 网球检测 + 插值 + 击球检测 + 球速/跑动速度
│   │   ├── video_renderer.py     # 轨迹视频渲染（Catmull-Rom 样条 + ffmpeg H.264 pipe  + 球速/跑动渲染）
│   │   ├── racket_tracking.py    # 球拍检测
│   │   ├── court_line_detector.py  # ResNet50 场地关键点检测（14 关键点）
│   │   ├── player_tracker.py     # YOLO 球员检测与跟踪
│   │   ├── mini_court.py         # 小场地坐标系映射（像素↔米转换）
│   │   ├── motion_analysis.py    # 运动检测核心算法
│   │   ├── orchestrator.py       # 后台任务调度
│   │   ├── pose_analysis.py      # 姿态分析
│   │   ├── ffmpeg_utils.py       # ffmpeg 工具
│   │   └── video_editor.py       # 视频剪辑
│   └── routers/                  # API 路由
│       ├── upload.py / videos.py / process.py / status.py
│       ├── analysis.py / ball.py / pose.py
│       ├── segments.py / export.py / download.py
│       └── upload.py
├── data/
│   ├── uploads/                  # 上传的原始视频
│   ├── analysis/                 # 分析结果 artifact (*.ball.json.gz)
│   ├── ball_videos/              # 轨迹渲染 MP4 输出
│   ├── exports/                  # 导出的剪辑视频
│   ├── segments_tmp/             # 临时片段缓存
│   ├── app.db                    # SQLite 数据库
│   └── static/                   # 前端静态资源
├── models/
│   ├── yolo5_last.pt             # YOLOv5 网球检测模型 (165 MB)
│   ├── keypoints_model.pth       # 关键点模型 (90 MB)
│   ├── yolo11n-pose.pt           # YOLO11 姿态模型 (6 MB)
│   └── yolov8n.pt                # YOLOv8 通用模型 (6 MB)
├── tests/                        # Python unittest 测试
├── static/                       # 前端代码
│   ├── index.html
│   ├── style.css
│   └── app.js
├── scripts/                      # 运维脚本
│   ├── restart.sh                # 服务启停
│   └── test.sh                   # 统一运行测试
├── requirements.txt              # 唯一依赖管理
├── CHANGELOG.md                  # 按日期版本记录
└── AGENT.md                      # 本文件（AI 助手指南）
```

## 代码规范

### Python 风格

- **类型注解**：所有函数参数和返回值必须标注类型（参考 `ball_tracking.py`）
- **文档字符串**：每个函数/方法必须写 docstring，中文描述，包含参数和返回说明
- **日志**：使用 `logging.getLogger(__name__)`，不要用 `print`
- **导入**：标准库 → 第三方库 → 本地模块，每组空行分隔
- **变量命名**：蛇形（`snake_case`），类名驼峰（`PascalCase`）
- **类型别名**：复杂类型用 `TypeAlias` 或 `typing` 别名，如 `ProgressCallback = Callable[[float, str, float | None], None]`

### 网球轨迹数据格式

```python
# 每帧的检测结果
ball_detections: list[dict[int, list[float]]]
# 格式: [{track_id: [x1, y1, x2, y2]}, ...]
# track_id 是 YOLO 跟踪 ID，多球时每个球有唯一 ID
```

### 击球时刻格式

```python
hit_moments: list[dict] = [
    {
        "frame_idx": int,
        "time_s": float,          # 击球时刻（秒）
        "ball_pos": (float, float),  # 球中心坐标 (x, y)
        "detection_method": str,  # "trajectory_analysis" | "racket_detection"
        "player_id": int | None,  # 击球球员的 track_id（由 _calculate_speed_events 分配）
    },
    ...
]
```

### Artifact 格式

```python
artifact = {
    "metadata": {
        "detector": "ball_tracking_yolo",
        "detector_version": 7,  # v7: 新增 players 汇总字段
        "model_path": "models/yolo5_last.pt",
        "video_width": int, "video_height": int,
        "video_fps": float, "duration_s": float,
        "range_start_s": float, "range_end_s": float,
        "sample_fps": float, "sample_count": int,
    },
    "summary": {
        "total_samples": int,       # 总帧数
        "detected_frames": int,     # 有检测的帧数
        "detection_rate": float,    # 百分比 0~100
        "total_ball_instances": int, # 所有球实例总数（多球时 > total_samples）
        "avg_confidence": float,
    },
    "ball_detections": [{track_id: [x1,y1,x2,y2]}, ...],
    "hit_moments": [...],
    # v6 新增字段（可选，检测成功时存在）：
    "court_keypoints": [28个float],  # 14个场地关键点坐标 (x0,y0,x1,y1,...)
    "player_detections": [{track_id: [x1,y1,x2,y2]}, ...],  # 过滤后的2名球员检测框
    "speed_events": {  # 速度事件
        "near_player_id": int | None,  # 靠近镜头球员的 track_id
        "far_player_id": int | None,   # 远离镜头球员的 track_id
        "ball_speeds": [
            {
                "frame_idx": int,        # 击球帧
                "speed_ms": float,       # 球速 m/s
                "hitter_id": int,        # 击球球员的 track_id
                "player_bbox": [x1,y1,x2,y2],  # 击球球员的边界框
                "ball_pos": [x,y],       # 击球时球在原始帧中的位置
            },
        ],
        "player_speeds": [
            {
                "start_frame": int,
                "end_frame": int,
                "speed_ms": float,       # 跑动速度 m/s
                "player_id": int,        # 跑动球员的 track_id
                "start_pos": [x,y],      # 原始帧中的脚部起始位置
                "end_pos": [x,y],        # 原始帧中的脚部结束位置
            },
        ],
    },
    # v7 新增字段：
    "players": {  # 球员汇总数据（key 为 "player_{id}"）
        "player_{id}": {
            "player_id": int,        # 球员 track_id
            "role": "near"|"far"|"unknown",  # 镜头侧身份
            "ball_speed_events": [   # 该球员的击球事件（同 speed_events.ball_speeds 条目）
                { "frame_idx": int, "speed_ms": float, "hitter_id": int, "player_bbox": [...], "ball_pos": [...] },
            ],
            "running_speed_events": [  # 该球员的跑动事件（同 speed_events.player_speeds 条目）
                { "start_frame": int, "end_frame": int, "speed_ms": float, "player_id": int, "start_pos": [...], "end_pos": [...] },
            ],
            "stats": {
                "ball_speed_max": float | None,   # 最大击球球速 m/s
                "ball_speed_avg": float | None,   # 平均击球球速 m/s
                "ball_speed_count": int,          # 击球次数
                "running_speed_max": float | None, # 最大跑动速度 m/s
                "running_speed_avg": float | None, # 平均跑动速度 m/s
                "running_speed_count": int,        # 跑动速度采样次数
            },
        },
    },
}
```

### 速度事件计算

参考 Tennis_AI_mainland 的算法：
1. **小场地映射**：通过 `court_line_detector`（ResNet50）检测场地 14 关键点，再用 `mini_court` 将球员和球映射到小场地坐标系
2. **靠近镜头球员确定**：优先使用**网线分界法**（`_assign_player_ids_by_court_side`），基于关键点 12/13（网柱）的 y 均值作为网线，foot_y > net_y 为近端球员。回退到 foot_y 比较（`_find_near_side_player`）。
3. **球速**：相邻击球帧间球在小场地上的位移 × 实际网宽 / 小场地宽度 / 时间 → m/s。计算所有击球事件（不限球员），数据含 `hitter_id` 标识击球球员。
4. **跑动速度**：相邻击球帧间球员在小场地上的位移 × 实际网宽 / 小场地宽度 / 时间 → m/s。计算所有球员在击球间隔内的移动速度，数据含 `player_id` 标识球员。
5. **渲染过滤**：`_init_speed_overlays()` 通过 `near_player_id` 过滤，仅初始化近端球员的球速文本和跑动连线。
5. **小场地比例尺**：用 `DOUBLE_LINE_WIDTH`（10.97m）对标小场地绘制宽度

### 视频渲染器函数签名

```python
def draw_ball_trajectory_video(
    video_path, ball_detections, output_path,
    range_start_s=0.0, sample_fps=4.0,
    progress_cb=None, hit_moments=None,
    player_detections=None,      # v6 新增
    speed_events=None,           # v6 新增
) -> Path
```

渲染效果：
- **球速文本**：击球后在击球点右上方显示青色渐变文本 "P{n} XX m/s"（n=球员ID），持续约 0.8 秒
- **跑动连线**：在起点和终点之间画黄色连线，中点显示 "P{n} XX m/s"（n=球员ID），持续约 1.0 秒

## 多球跟踪（feature/multi-ball-tracking 分支）

### 关键修改

| 文件 | 改动 |
|------|------|
| `app/pipeline/ball_tracking.py` | `_detect_frame` 使用 `model.track()` 替代 `predict()`，获取稳定 `track_id` |
| | `_interpolate_ball_positions` 按 `track_id` 独立插值 |
| | `get_ball_shot_frames` 每个球分别分析击球，结果合并去重 |
| | `DETECTION_STEP=3` 帧跳跃检测，每3帧检测1帧，~3x加速（CPU环境） |
| `app/pipeline/video_renderer.py` | `trajectory_points: dict[int, list]` 按球 ID 分开存储轨迹 |
| | 每个球使用不同颜色（8 种循环），标记框显示 `Ball {id}` |

### 轨迹数据结构（渲染时）

```python
# 修改前（单球）
trajectory_points: list[tuple[int, int]] = []
ball_dict = {1: [x1, y1, x2, y2]}  # track_id 总是 1

# 修改后（多球）
trajectory_points: dict[int, list[tuple[int, int]]] = {}
ball_dict = {1: [x1, y1, x2, y2], 2: [x1, y1, x2, y2], ...}
```

### 球颜色分配

```python
trajectory_colors = [
    (0, 255, 255),    # 黄色
    (255, 0, 0),      # 蓝色
    (0, 255, 0),      # 绿色
    (255, 0, 255),    # 紫色
    (255, 128, 0),    # 青色
    (0, 128, 255),    # 橙色
    (128, 255, 255),  # 浅黄
    (255, 255, 0),    # 红色
]
# 通过 track_id % len(colors) 循环分配
```

### 帧跳跃检测（CPU 加速）

```python
# ball_tracking.py
DETECTION_STEP = 3  # 每 3 帧检测 1 帧，~3x 加速

# 主循环中：
if frame_idx % DETECTION_STEP == 0:
    ball_dict = _detect_frame(frame, model)  # 完整检测
else:
    ball_dict = {}  # 跳过：留空由插值补全

# 插值函数 _interpolate_ball_positions 自动补全所有跳过帧
```

- CPU 环境下每 3 帧仅检测 1 帧，其余由线性插值补全
- 实测加速比 ~3.0x（4.9fps → 14.6fps），插值后 100% 帧有坐标
- 球拍检测与球检测同步跳跃

## 测试规范

### 框架

- 使用 Python 内置 `unittest`，**不依赖 pytest**
- 前端测试用 `jsdom`（Node.js），通过 `tests/test_frontend_render.js`
- 统一运行命令：`bash scripts/test.sh`

### 测试文件命名

```
tests/test_<module_name>.py
```

### 测试约定

- 继承 `unittest.TestCase`
- 类方法 `setUpClass` / `tearDownClass` 做一次性准备/清理
- 实例方法 `setUp` / `tearDown` 做每次准备/清理
- 测试方法以 `test_` 开头
- 视频测试使用 `setUpClass` 跳过不存在的文件
- **测试资源文件必须参数化**：视频路径等资源通过命令行参数或函数参数传入，禁止硬编码（详见项目规则 `test-resource-params`）
- **不要修改 `app/config` 的全局状态**：必须保存原始值并在 `tearDown` / `finally` 中恢复

### 多球测试脚本

```bash
# 默认视频
PYTHONPATH=. python tests/test_multi_ball_tracking.py

# 指定视频 + 跳过扫描 + 自定义时间段
PYTHONPATH=. python tests/test_multi_ball_tracking.py path/to/video.mp4 --skip-scan --render-start 10.0

# 帮助
PYTHONPATH=. python tests/test_multi_ball_tracking.py --help
```

### 测试输出目录

| 输出 | 目录 |
|------|------|
| 分析结果 (*.ball.json.gz) | `data/analysis/` |
| 渲染视频 (*.mp4) | `data/ball_videos/` |

## 输入/输出路径

### Config (`app/config.py`)

```python
settings = Settings()  # pydantic-settings
# 关键路径：
#   upload_dir      → data/uploads
#   analysis_dir    → data/analysis       (ball.json.gz)
#   ball_videos_dir → data/ball_videos    (轨迹渲染视频)
#   exports_dir     → data/exports
#   db_path         → data/app.db
```

### 模型路径

```python
# ball_tracking.py
BALL_MODEL_PATH = Path(__file__).parent.parent.parent / "models" / "yolo5_last.pt"
```

### 所有测试使用 PYTHONPATH

```bash
PYTHONPATH=/workspace/tennis-highlight:$PYTHONPATH python ...
```

## 启动与运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
bash scripts/restart.sh

# 测试
bash scripts/test.sh
```

## Git 工作流

- **分支策略**：功能分支 + main 主线
- **开发完成收尾流程**（每次修改完成必须按顺序执行）：
  1. 确认代码修改完成，测试通过
  2. **更新 CHANGELOG.md**：在文件最顶部插入新日志条目（格式见下方）
  3. **Git 提交**：`git add <文件> && git commit -m "<type>: <中文描述>"`
  4. **同步 AGENT.md**：如需（参考 `sync-agent-md` 规则）
- **提交消息格式**：`<type>: <中文描述>`
  - `feat:` — 新功能
  - `refactor:` — 重构
  - `fix:` — 修复
  - `test:` — 测试
  - `docs:` — 文档
  - `chore:` — 杂项
- **CHANGELOG 条目格式**：按日期记录（`YYYY-MM-DD HH:MM:SS (UTC+8)`），不要用语义化版本

  ```markdown
  ## YYYY-MM-DD HH:MM:SS (UTC+8)

  ### type: 简短标题

  修改文件：
  | 文件 | 修改内容 |
  |------|----------|
  | path/to/file.py | 改动描述 |

  ### 详细说明

  1. 做了什么
  2. 怎么做的
  3. 预期效果

  ---
  ```

## 禁止事项

1. **不要修改 main 分支** — 所有改动在功能分支进行
2. **不要硬编码视频路径** — 测试脚本通过命令行参数传入
3. **不要全局修改 config** 而不恢复 — 必须用 try/finally 或 setUp/tearDown 恢复
4. **不要使用 pytest** — 项目统一使用 unittest
5. **不要创建 pyproject.toml/setup.py** — 唯一依赖管理是 requirements.txt
6. **不要提交模型文件到 Git** — 模型文件体积大，需单独下载
