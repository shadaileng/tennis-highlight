# Tennis Highlight Extractor — 网球高光提取器

一个从业余网球训练视频中自动去除死球时间，并提供**高级球/球员轨迹分析**的 Web 应用。

用户上传固定三脚架拍摄的视频，应用通过运动分析自动检测"比赛进行中"和"死球时间"的时间段。在交互式编辑器中审查/调整分段边界、预览效果，最终导出仅包含有效回合的单个 MP4 文件。

除高光提取外，应用还支持 **YOLO 网球检测**、**球场校准（单应性矩阵）**、**球速/球员跑动速度计算**，以及**可组合渲染管线**——在输出视频上绘制轨迹线、火花特效、速度标签、骨骼骨架、落地点标记和迷你球场俯视图。

---

## 功能特性

- **运动检测高光提取** — 中值帧背景减法自动检测有效比赛片段
- **交互式分段编辑器** — 在浏览器中切换、预览、调整分段边界
- **球场校准** — 手动标记 4 个角点 → 单应性变换 → 14 个标准关键点
- **网球轨迹跟踪** — YOLOv5 检测 + 轨迹插值 + 击球检测
- **球速显示** — 在球员附近显示击球速度（m/s）
- **球员跑动速度** — 击球点之间的跑动速度及连线
- **球员骨骼骨架** — YOLO11-pose 每帧骨骼渲染
- **落地点标记** — 落球点的冲击波动画效果
- **迷你球场地图** — 右下角鸟瞰小场地视图
- **可组合渲染管线** — 8 个独立渲染层，可分别开关
- **多球跟踪** — 按球 ID 分别着色跟踪
- **导出功能** — 将高光片段合成为单个 MP4，或下载轨迹渲染视频

---

## 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| **Python** | 3.12+ | 在 3.14 上开发 |
| **ffmpeg** | 最新 | 需在 `PATH` 环境变量中 |
| **Node** | 18+ | 可选 — 仅用于前端 DOM 测试 |

---

## 安装步骤

```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装 Python 依赖
pip install -r requirements.txt

# （可选）安装 DOM 测试依赖
npm install --no-save jsdom
```

### ML 模型

将以下模型文件放入 `models/` 目录：

| 模型 | 文件 | 大小 | 用途 |
|---|---|---|---|
| YOLOv5 | `yolo5_last.pt` | ~165 MB | 网球检测 |
| ResNet50 | `keypoints_model.pth` | ~90 MB | 球场关键点检测（备选） |
| YOLO11-pose | `yolo11n-pose.pt` | ~6 MB | 球员骨骼姿态 |
| YOLOv8 | `yolov8n.pt` | ~6 MB | 球员检测与跟踪 |

---

## 启动服务

```bash
uvicorn app.main:app --port 8000
# 打开 http://localhost:8000

# 热重载模式
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 启用调试日志
VERBOSE=true uvicorn app.main:app --port 8000
```

或使用管理脚本：

```bash
bash scripts/restart.sh start    # 启动
bash scripts/restart.sh stop     # 停止
bash scripts/restart.sh status   # 状态
bash scripts/restart.sh logs     # 查看日志
```

---

## 运行测试

```bash
bash scripts/test.sh
```

包含：
1. **Python 测试**（`unittest`）— 管线测试、契约测试，约 23 个
2. **前端 DOM 测试**（Node + `jsdom`）— 运行时 UI 测试，约 14 个

DOM 测试在未安装 `jsdom` 时会自动跳过。

单独运行：

```bash
.venv/bin/python -m unittest discover tests/ -v   # Python 测试
node tests/test_frontend_render.js                # 前端 DOM 测试
```

---

## API 接口

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/` | 首页（SPA 单页应用） |
| `POST` | `/upload` | 上传视频（按哈希去重） |
| `GET` | `/videos` | 列出所有视频 |
| `GET` | `/media` | 列出所有媒体资产 |
| `GET` | `/status/{id}` | 视频处理状态 |
| `DELETE` | `/videos/{id}` | 删除视频（级联删除） |
| `POST` | `/process/{id}` | 启动分析（可选算法、参数、时间范围） |
| `GET` | `/analyses` | 列出所有分析记录 |
| `GET` | `/analysis-status/{id}` | 分析状态 |
| `DELETE` | `/analyses/{id}` | 删除分析 |
| `GET` | `/analysis/{id}` | 获取分析详情（含分段结果） |
| `POST` | `/analysis/{id}/preview` | 预览分析（调整参数后重算） |
| `GET` | `/analysis/{id}/ball-video` | 下载轨迹渲染视频 |
| `GET` | `/analysis-segments/{id}` | 获取分析的分段 |
| `GET` | `/segments/{id}` | 获取视频分段 |
| `PATCH` | `/segments/{id}` | 保存分段修订 |
| `GET` | `/video-file/{id}` | 获取视频 URL（自动转码 H.264） |
| `GET` | `/calibration-frame/{id}` | 提取首帧用于球场校准 |
| `PUT` | `/media/{id}/court-calibration` | 保存 4 角点球场校准 |
| `POST` | `/ball-trajectory/{id}` | 启动网球轨迹分析 |
| `POST` | `/ball-trajectory-from-video/{id}` | 从视频直接启动轨迹分析 |
| `GET` | `/ball-trajectory/{id}` | 获取轨迹数据 |
| `GET` | `/pose-data/{id}` | 获取姿态数据 |
| `POST` | `/export/{id}` | 启动导出 |
| `GET` | `/export-status/{id}` | 导出状态 |
| `GET` | `/download/{id}` | 下载导出视频 |

---

## 检测算法

| 算法 | 原理 | 适用场景 |
|---|---|---|
| `median_frame` | 全视频逐像素中值 → "空球场"背景，每帧与背景做差值 | 默认算法，固定三脚架拍摄。无预热期。 |
| `median_court_roi` | 同上，但在球场多边形区域内/外加权 | 有人在镜头前走动导致误报时 |
| `pose_skeleton_yolo` | YOLO11-pose 逐帧检测球员骨骼骨架 | 运动检测漏掉小/远球员时 |
| `ball_tracking_yolo` | YOLOv5 检测 + 轨迹插值 + 击球检测 | 高级分析：轨迹、球速、落地点 |

---

## 渲染管线

8 个可组合的渲染层，每个可通过 API 参数独立开关：

| 渲染层 | 渲染内容 | 默认 |
|---|---|---|
| `BallTrajectoryPass` | 网球轨迹线 + 检测框 | 开 |
| `SparkPass` | 击球火花菱形飞出效果 | 开 |
| `BallSpeedPass` | 球速文本 "Pn XX m/s" | 开 |
| `RunningSpeedPass` | 跑动速度连线 + 文本 | 开 |
| `SkeletonPass` | 青色骨骼关键点骨架 | 开 |
| `LandingMarkerPass` | 落地点黄色十字标记 + 冲击波 | 开 |
| `CourtAnimationPass` | 开场 1.5s 一笔画成球场动画 | 开 |
| `MiniCourtPass` | 右下角迷你球场俯视图（280×340px） | 开 |

---

## 配置

配置由 `pydantic-settings` 管理（文件 `app/config.py`），可通过 `.env` 文件或环境变量覆盖：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `upload_dir` | `data/uploads` | 上传目录 |
| `exports_dir` | `data/exports` | 导出目录 |
| `analysis_dir` | `data/analysis` | 分析结果目录 |
| `ball_videos_dir` | `data/ball_videos` | 轨迹渲染视频目录 |
| `db_path` | `data/app.db` | SQLite 数据库路径 |
| `motion_threshold` | `0.02` | 运动评分阈值 |
| `merge_gap_s` | `3.0` | 分段合并间隔（秒） |
| `min_segment_s` | `5.0` | 最小分段长度（秒） |
| `segment_padding_s` | `1.0` | 分段边缘填充（秒） |
| `sample_fps` | `2.0` | 分析采样帧率 |
| `diff_threshold` | `25` | 背景差异阈值（0-255） |
| `median_bg_samples` | `80` | 中值背景采样帧数 |
| `progress_report_interval_s` | `5.0` | 进度回调间隔 |

---

## 项目结构

```
app/
  main.py              # FastAPI 入口
  config.py            # pydantic-settings 配置
  database.py          # SQLite CRUD
  models.py            # Pydantic 请求/响应模型
  routers/             # HTTP 路由
    upload.py          # POST /upload 上传
    videos.py          # GET /videos, /media, PUT court-calibration, DELETE
    process.py         # POST /process, GET /analyses, /analysis-status
    status.py          # GET /status/{id}
    analysis.py        # GET /analysis/{id}, /analysis/{id}/preview, ball-video
    segments.py        # GET/PATCH /segments/{id}
    ball.py            # POST /ball-trajectory, /ball-trajectory-from-video
    pose.py            # GET /pose-data/{id}
    export.py          # POST /export/{id}
    download.py        # GET /download/{id}
  pipeline/            # 核心处理管线
    motion_analysis.py     # 中值背景减法
    ball_tracking.py       # YOLOv5 网球检测 + 轨迹 + 速度
    ball_tracker.py        # BallTracker 封装类
    pose_analysis.py       # YOLO11-pose 骨骼检测
    court_homography.py    # 手动 4 角点 → Homography → 14 关键点
    court_line_detector.py # ResNet50 自动球场检测（备选）
    player_tracker.py      # YOLOv8 球员检测与跟踪
    racket_tracking.py     # 球拍检测
    mini_court.py          # 像素↔米球场坐标映射
    render_pipeline.py     # 可组合渲染管线编排器
    video_renderer.py      # 管线装配 + ffmpeg pipe 输出
    video_editor.py        # ffmpeg 片段提取 + 拼接
    ffmpeg_utils.py        # ffmpeg/ffprobe 工具函数
    orchestrator.py        # 后台任务调度器（ProcessPoolExecutor）
static/
  index.html           # 单页 UI
  app.js               # 前端全部逻辑（~48 KB）
  style.css            # 样式表
tests/                 # Python + Node 测试
scripts/
  test.sh              # 统一运行测试
  restart.sh           # 服务管理
  clear_analysis.sh    # 清除分析数据
data/                  # 运行时数据（gitignored）
  uploads/             # 上传的原始视频
  analysis/            # 分析结果 (*.ball.json.gz)
  ball_videos/         # 轨迹渲染视频
  exports/             # 导出的剪辑视频
  segments_tmp/        # 临时片段缓存
  app.db               # SQLite 数据库
models/                # ML 模型权重（gitignored）
```

---

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI (Python 3.12+) |
| Web 服务器 | Uvicorn |
| 视频处理 | OpenCV, ffmpeg/ffprobe |
| ML 推理 | Ultralytics (YOLOv5/v8/11), ResNet50 |
| 数据处理 | NumPy, Pandas, SciPy |
| 数据库 | SQLite |
| 前端 | 纯 JS + HTML + CSS（无框架） |
| 测试 | Python `unittest` + Node `jsdom` |

---

## 环境变量

- `VERBOSE=true` — 启用 DEBUG 级别日志
- `.env` 文件 — 覆盖 `app/config.py` 中任意配置

---

## Docker

当前无 Dockerfile — 请参考[安装步骤](#安装步骤)进行本地安装。

---

## 许可

MIT License
