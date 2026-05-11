# 开发日志 (CHANGELOG)

## 2026-05-12 (UTC+8)

### 优化：网球轨迹跟随平滑度优化 - 时间补偿 + 线性插值

**问题描述：**
网球轨迹跟随存在轻微延迟，运动跳跃不够平滑。

**根本原因：**
1. `video.currentTime` 存在系统级延迟，需要时间补偿对齐
2. 帧间运动跳跃明显，需要插值平滑

**解决方案：**
1. 增加时间补偿从 1 帧 → 2 帧（约 0.067 秒 @ 30fps）
2. 添加线性插值，在最新检测点和当前时间之间平滑过渡

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `static/app.js` | `drawBallTrail()` 函数添加时间补偿和线性插值 |

**核心代码变更：**

```javascript
// app.js - 时间补偿（第1523-1525行）
const timeOffset = 2 / 30; // 约0.067秒 = 2帧 @ 30fps
const currentTime = Math.min((video.currentTime || 0) + timeOffset, state.duration);

// 线性插值：添加当前时间的预测点使运动更平滑（第1573-1592行）
if (trailPoints.length >= 2) {
  const latest = trailPoints[trailPoints.length - 1];
  const prev = trailPoints[trailPoints.length - 2];
  const gap = latest.time_s - prev.time_s;

  if (gap > 0) {
    const t = (currentTime - latest.time_s) / gap;
    if (t > 0 && t <= 1) {
      // 在最新点和预测点之间插值
      const interpX = latest.x + (latest.x - prev.x) * t * 0.5;
      const interpY = latest.y + (latest.y - prev.y) * t * 0.5;
      trailPoints.push({
        time_s: currentTime,
        x: interpX,
        y: interpY
      });
    }
  }
}
```

**效果：**
- 轨迹跟随延迟减少
- 运动更加平滑连续
- 消除帧间跳跃感

---

## 2026-05-12 (UTC+8)

### 修复：球轨迹 API 返回空数据问题 - JSON key 类型兼容

**问题描述：**
`GET /ball-trajectory/{analysis_id}` 接口返回的 `trajectory` 列表始终为空，虽然逐帧检测时网球被正确标记。

**根本原因：**
`ball.py` 第 94 行使用整数 `1` 检查 key，但 `ball.json.gz` 中存储的 key 是字符串 `"1"`（JSON 序列化导致）。

```python
# 原代码 - key 类型不匹配
if 1 in det:  # False，因为 key 是 "1"
    x1, y1, x2, y2 = det[1]
```

**解决方案：**
兼容整数和字符串两种 key 类型。

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `app/routers/ball.py` | 兼容 `det.get(1)` 和 `det.get("1")` |

**核心代码变更：**

```python
# ball.py - 兼容字符串和整数 key
for i, det in enumerate(ball_detections):
    bbox = det.get(1) or det.get("1")
    if bbox is not None:
        x1, y1, x2, y2 = bbox
```

**测试结果：**
- 新增测试文件 `tests/test_ball_trajectory_api_coordinates.py`
- 10 个测试用例全部通过
- API 返回 213 个轨迹点，检测率 100%

---

## 2026-05-12 (UTC+8)

### 修复：网球轨迹不同步问题 - 逐帧检测 + 归一化坐标

**问题描述：**
网球轨迹跟随不同步，检测和渲染的时间戳映射错位。

**根本原因：**
1. 原检测逻辑每帧都检测，但时间戳计算错误
2. 原渲染逻辑使用 `int()` 取整导致帧到检测索引映射错误
3. 检测读取了整个视频而非指定范围

**解决方案：**
- 逐帧检测，使用视频实际帧率
- 移除前端 `sample_fps` 参数传递
- 修复检测范围，只读取 `range_start_s` 到 `range_end_s`
- 坐标归一化返回，前端 canvas 根据尺寸渲染

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/ball_tracking.py` | 逐帧检测，`sample_fps = video_fps`，只检测指定范围 |
| `app/pipeline/video_renderer.py` | 逐帧绘制，`frame_idx` 直接作为检测索引 |
| `app/routers/ball.py` | 移除 API 中 `sample_fps` 参数 |
| `static/app.js` | 移除前端 `sample_fps` 参数传递 |

**核心代码变更：**

```python
# ball_tracking.py - 逐帧检测指定范围
num_target_frames = int(duration * video_fps)  # 实际要检测的帧数
while frame_idx < num_target_frames:
    ret, frame = cap.read()
    ball_dict = _detect_frame(frame, model)
    ball_detections.append(ball_dict)
    frame_idx += 1

# metadata 中 sample_fps = video_fps
metadata = {
    "sample_fps": video_fps,  # 逐帧检测
    ...
}

# ball.py - API 不再接收 sample_fps
class BallFromVideoRequest(BaseModel):
    range_start_s: float | None = None
    range_end_s: float | None = None
```

```javascript
// app.js - 前端不传递 sample_fps
body: JSON.stringify({ 
    range_start_s: rangeStart,
    range_end_s: rangeEnd,
}),
```

**坐标系统：**

| 层级 | 格式 | 示例 |
|------|------|------|
| 原始像素 | `[x1, y1, x2, y2]` | `[100, 200, 150, 250]` |
| 归一化 (0-1) | `{x, y, bbox}` | `{x: 0.05, y: 0.18}` |
| Canvas 像素 | `p.x * canvasWidth` | `960 * 0.05 = 48` |

**效果：**
- 检测和渲染完全同步
- 轨迹正确跟随网球
- 拖尾效果基于归一化坐标正确渲染

---

## 2026-05-09 19:15:00 (UTC+8)

### 优化：网球轨迹跟随紧密性优化 - 添加轨迹插值

**问题描述：**
网球轨迹跟随不够紧密，当某帧未检测到网球时轨迹断裂。

**参考项目：**
https://github.com/qchen34/Tennis_AI_mainland

**解决方案：**
使用 pandas 线性插值填充未检测到的帧，使轨迹连续平滑。

**修改文件：**

1. `app/pipeline/ball_tracking.py`

**参数变更：**

| 参数 | 原来 | 现在 | 说明 |
|------|------|------|------|
| 置信度阈值 `conf` | 0.08 | 0.15 | 提高阈值减少误检 |
| 追踪距离阈值 | 0.3 | 0.15 | 更严格减少误检 |

**核心代码变更：**

```python
# 1. 新增 pandas 依赖
import pandas as pd

# 2. 降低追踪距离阈值
TRACKING_DISTANCE_THRESHOLD = 0.15

# 3. 提高检测置信度
results = model.predict(frame, conf=0.15, verbose=False)[0]

# 4. 新增轨迹插值函数
def _interpolate_trajectory(trajectory_points: list[dict]) -> list[dict]:
    """使用 pandas 插值填充缺失的球位置，使轨迹更平滑连续"""
    if not trajectory_points:
        return trajectory_points
    
    # 转换为 DataFrame 格式
    rows = []
    for p in trajectory_points:
        if p["x"] is not None and p["y"] is not None:
            rows.append({...})
        else:
            rows.append({"x": np.nan, "y": np.nan, ...})
    
    df = pd.DataFrame(rows)
    
    # 线性插值填充缺失值
    df['x'] = df['x'].interpolate(method='linear')
    df['y'] = df['y'].interpolate(method='linear')
    
    # 处理首尾的 NaN 值（bfill/ffill）
    df['x'] = df['x'].bfill().ffill()
    df['y'] = df['y'].bfill().ffill()
    
    return result

# 5. 保存前应用插值
interpolated_trajectory = _interpolate_trajectory(trajectory_points)
artifact = {
    "trajectory": interpolated_trajectory,  # 使用插值后的轨迹
    ...
}
```

**效果：**
- 未检测到网球的帧通过线性插值计算位置
- 轨迹不会断裂，更加平滑连续
- 提高检测置信度减少误检

---

## 2026-05-09 18:30:00 (UTC+8)

### 优化：分析任务改为后台进程执行，避免服务卡死

**问题描述：**
网球轨迹分析（ball_tracking_yolo）使用 YOLO 模型进行 CPU 密集型推理时，通过 `asyncio.to_thread()` 执行仍会阻塞服务，导致其他请求无响应。

**解决方案：**
使用 `ProcessPoolExecutor` 替代线程池，将 CPU 密集型任务放入独立进程中执行。

**修改文件：**

1. `app/pipeline/orchestrator.py`

| 改动 | 原来 | 现在 |
|------|------|------|
| 执行器类型 | `asyncio.to_thread()` | `loop.run_in_executor(ProcessPoolExecutor)` |
| 隔离方式 | 线程（受 GIL 限制） | 进程（完全独立） |

**核心代码变更：**

```python
# 新增进程池管理
from concurrent.futures import ProcessPoolExecutor

_process_pool: ProcessPoolExecutor | None = None

def _get_process_pool() -> ProcessPoolExecutor:
    global _process_pool
    if _process_pool is None:
        import multiprocessing
        max_workers = min(multiprocessing.cpu_count(), 4)
        _process_pool = ProcessPoolExecutor(max_workers=max_workers)
        logger.info(f"Created process pool with {max_workers} workers")
    return _process_pool

# run_analysis 中的调用方式
loop = asyncio.get_running_loop()
await loop.run_in_executor(
    _get_process_pool(),
    lambda: analyze_ball_trajectory(...)
)
```

**影响范围：**
- `ball_tracking_yolo` 分析算法
- `pose` 分析算法
- `median_frame` / `median_court_roi` 分析算法

**效果：**
- 分析任务在独立进程中执行，不阻塞 FastAPI 事件循环
- 服务可正常响应其他 HTTP 请求
- 最多支持 4 个并发分析任务

---

## 2026-05-09 17:15:00 (UTC+8)

### 修复：网球轨迹分析 API 错误 & 新增端到端测试

**问题描述：**
调用 `GET /analysis/{analysis_id}` 获取网球轨迹分析结果时返回 500 错误：
```
UnboundLocalError: cannot access local variable 'knobs' where it is not associated with a value
```

**根本原因：**
`app/routers/analysis.py` 中 `BALL_ALGORITHM` 分支未初始化 `knobs` 变量，且 `metadata` 缺少 `median_bg_samples` 字段。

**修改文件：**

1. `app/routers/analysis.py`
   - 第 112 行：添加 `knobs = {}` 初始化
   - 第 113-114 行：添加 `median_bg_samples` 默认值处理

```python
elif analysis["algorithm"] == BALL_ALGORITHM:
    artifact = load_ball_artifact(artifact_path)
    metadata = artifact.get("metadata", {})
    summary = artifact.get("summary", {})
    knobs = {}
    # Ball metadata doesn't have median_bg_samples, set default
    if "median_bg_samples" not in metadata:
        metadata["median_bg_samples"] = 0
```

2. 新增测试文件：`tests/test_ball_trajectory_e2e.py`
   - 完整端到端测试覆盖网球轨迹分析流程
   - 6 个测试用例全部通过

**测试用例：**

| 测试名称 | 描述 | 状态 |
|---------|------|------|
| `test_full_workflow_ball_trajectory_analysis` | 完整工作流测试 | ✓ |
| `test_analysis_endpoint_returns_ball_algorithm` | API 端点测试 | ✓ |
| `test_workflow_with_process_endpoint` | 替代工作流测试 | ✓ |
| `test_get_nonexistent_trajectory` | 空数据返回测试 | ✓ |
| `test_start_analysis_with_invalid_video` | 404 错误测试 | ✓ |
| `test_ball_trajectory_response_schema` | 响应结构验证 | ✓ |

**新增依赖：**
```bash
pip install ultralytics
```

**服务重启：** 2026-05-09 17:15:00 (UTC+8)

---

## 2026-05-09 14:32:15 (UTC+8)

### 优化：网球检测从 HSV 颜色分割升级为 YOLO 深度学习模型

**功能描述：**
将网球检测算法从 HSV 颜色分割替换为 YOLOv5 深度学习模型，提升检测准确性和鲁棒性。

**修改文件：**

1. `app/pipeline/ball_tracking.py`
   - 新增 `_get_ball_model()` 函数：懒加载 YOLO 模型，全局缓存避免重复加载
   - 新增 `_detect_tennis_ball_yolo()` 函数：使用 YOLO 模型检测网球，置信度阈值 0.15
   - 修改 `analyze_ball_trajectory()` 函数：使用 YOLO 模型替代 HSV 颜色检测
   - 删除旧的 `_detect_tennis_ball_hsv()` 函数和 HSV 相关常量

**模型文件说明：**

| 模型文件 | 用途 | 来源 |
|---------|------|------|
| `models/yolo5_last.pt` | 网球检测模型 (YOLOv5) | tennis_analysis 项目 |
| `models/keypoints_model.pth` | 场地关键点模型 | tennis_analysis 项目 |

**配置变更：**

| 项目 | 原来 (HSV) | 现在 (YOLO) |
|------|-----------|-------------|
| 算法名称 | `ball_tracking_color` | `ball_tracking_yolo` |
| 版本号 | 2 | 3 |
| 检测方式 | HSV 颜色分割 + 轮廓分析 | YOLOv5 模型推理 |
| bbox 来源 | 固定值 `cx±0.01` | YOLO 检测的真实边界框 |

**新增依赖：**
```bash
pip install ultralytics
```

**代码示例：**

```python
# 模型加载 (懒加载)
model = _get_ball_model()

# YOLO 推理
results = model.predict(frame, conf=0.15, verbose=False)[0]
boxes = results.boxes
x1, y1, x2, y2 = boxes.xyxy[best_idx].cpu().numpy()
```

**影响范围：**
- 网球检测准确性提升，特别是复杂背景下的检测
- bbox 数据现在包含真实的边界框坐标
- metadata 中新增 `model_path` 字段记录使用的模型路径

**服务重启：** 2026-05-09 14:35:22 (UTC+8)
- 服务运行于 `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- 日志输出至 `logs/uvicorn.log`

---

## 2026-05-09 10:19:11 (UTC+8)

### 功能：支持取消 pending 状态的分析记录

**修改内容：**

1. `static/app.js` - 前端界面
   - 第 215-220 行：为 `pending` 和 `analyzing` 状态的分析记录添加 "Cancel" 按钮
   - 第 235 行：修改删除确认消息，根据状态显示 "Cancel" 或 "Delete"

**代码变更：**

```javascript
// 为 pending 和 analyzing 状态添加取消按钮
if (a.status === "pending" || a.status === "analyzing") {
  const cancel = document.createElement("button");
  cancel.textContent = "Cancel";
  cancel.style.marginLeft = "6px";
  cancel.addEventListener("click", () => deleteAnalysis(a));
  actTd.appendChild(cancel);
}

// 动态确认消息
const action = a.status === "pending" || a.status === "analyzing" ? "Cancel" : "Delete";
if (!confirm(`${action} analysis "${a.algorithm}" for "${a.filename}"?`)) return;
```

**影响范围：**
- 用户可以取消等待中的分析任务
- pending 和 analyzing 状态的记录右上角显示 "Cancel" 按钮
- 点击后弹出确认对话框

---

## 2026-05-09 09:45:03 (UTC+8)

### 修复：球轨迹分析状态卡在 pending 的问题

**问题描述：**
前端点击"Analyze Ball Trajectory"按钮后，分析状态一直显示为 pending，无法变为 done。

**根本原因：**
`app/routers/ball.py` 中的后台任务没有正确更新分析状态：
1. 没有设置状态为 `"analyzing"`
2. 完成后没有调用 `complete_analysis()` 设置为 `"done"`

对比 `app/pipeline/orchestrator.py` 的正确实现，发现缺少状态更新逻辑。

**修改文件：**

1. `app/routers/ball.py`
   - 第 9 行：新增导入 `complete_analysis`
   - 第 44-63 行（`start_ball_analysis` 函数）：
     - 添加 `await update_analysis_status(ball_analysis_id, "analyzing")`
     - 添加 `await complete_analysis(...)` 完成状态更新
   - 第 124-143 行（`start_ball_analysis_from_video` 函数）：
     - 添加 `await update_analysis_status(ball_analysis_id, "analyzing")`
     - 添加 `await complete_analysis(...)` 完成状态更新

**关键代码变更：**

```python
async def run():
    try:
        await update_analysis_status(ball_analysis_id, "analyzing")
        artifact_path, _ = analyze_ball_trajectory(...)
        await complete_analysis(
            ball_analysis_id,
            artifact_path=str(artifact_path),
            instant_knobs_json=json.dumps({"sample_fps": req.sample_fps}),
        )
    except Exception as e:
        logger.exception("Ball trajectory analysis failed")
        await update_analysis_status(ball_analysis_id, "error", str(e))
```

**影响范围：**
- 前端现在能正确显示分析进度：pending → analyzing → done
- 用户可以看到真实的分析状态而非一直等待

---

## 2026-05-09

### 新增：网球轨迹拖尾功能 (Ball Trajectory Trail Effect)

**功能描述：**
在视频播放时实时显示球的运动轨迹，包含粒子拖尾效果和发光线条效果。

**新增文件：**

1. `app/pipeline/ball_tracking.py`
   - 颜色分割检测模块 (HSV 黄色-绿色网球检测)
   - 圆形度过滤排除噪声
   - 轨迹追踪保持检测连续性
   - 返回检测到的球坐标序列

2. `app/routers/ball.py`
   - API 路由，提供球轨迹分析接口
   - `POST /api/ball/analyze` - 分析视频中的球轨迹
   - `GET /api/ball/trajectory` - 获取轨迹数据

**技术说明：**
- 初始使用 YOLO COCO class 32 检测率 0%（标准模型不适用于网球）
- 改用 HSV 颜色分割 + 圆形度过滤后检测率达 100%
- 后续可考虑训练自定义 YOLO 模型进一步提升准确性

**修改文件：**

1. `app/main.py`
   - 注册 ball 路由

2. `static/index.html`
   - 新增 `ball-trail-canvas` 独立 Canvas 层
   - 新增轨迹控制面板（Show trail 开关、Analyze Ball Trajectory 按钮）

3. `static/style.css`
   - Canvas 定位样式（覆盖层定位）

4. `static/app.js`
   - 粒子拖尾绘制逻辑
   - 轨迹数据加载
   - Analyze 按钮点击事件处理

**功能特性：**

| 功能 | 描述 |
|------|------|
| 粒子效果 | 球位置生成黄色粒子，含随机速度和衰减 |
| 发光拖尾线 | 渐变透明度的轨迹连接线 |
| 实时绘制 | 视频播放时同步更新轨迹 |
| 控制开关 | Show trail 勾选控制显示/隐藏 |
| 一键分析 | 点击按钮启动 YOLO 检测 |

**使用流程：**
1. 打开已有分析结果
2. 点击 "Analyze Ball Trajectory" 按钮
3. 等待分析完成（YOLO 检测球位置）
4. 勾选 "Show trail" 开关
5. 播放视频查看粒子拖尾效果

---

## 2026-05-08

### 修复：FFmpeg 跨平台硬件加速支持

**问题描述：**
FFmpeg 在 Linux 系统上使用 `-hwaccel videotoolbox` (macOS 专用硬件加速) 导致解码失败：
```
Device creation failed: -12. No device available for decoder: device type videotoolbox needed for codec h264
```

**修改文件：**

1. `app/pipeline/ffmpeg_utils.py`
   - 新增 `hwaccel_args()` 函数，根据操作系统自动选择合适的硬件加速方式
   - macOS (Darwin): 返回 `["-hwaccel", "videotoolbox"]`
   - Windows: 返回 `["-hwaccel", "dxva2"]`
   - Linux/其他: 返回空列表 (使用软件解码)

2. `app/pipeline/pose_analysis.py`
   - 第 181 行：将硬编码的 `-hwaccel videotoolbox` 替换为 `*hwaccel_args()`
   - 添加了 `from app.pipeline.ffmpeg_utils import hwaccel_args` 导入

3. `app/pipeline/motion_analysis.py`
   - 第 199 行 (phase 1)：将硬编码的 `-hwaccel videotoolbox` 替换为 `*hwaccel_args()`
   - 第 283 行 (phase 2)：将硬编码的 `-hwaccel videotoolbox` 替换为 `*hwaccel_args()`
   - 添加了 `from app.pipeline.ffmpeg_utils import hwaccel_args` 导入

**影响范围：**
- 修复了在 Linux 环境运行时的 FFmpeg 解码失败问题
- 保持了 macOS 用户的硬件加速功能
- 为 Windows 用户添加了 DXVA2 硬件加速支持

**后续优化方向：**
- 可在 `config.py` 中添加 `hwaccel_enabled` 开关，允许用户手动控制是否启用硬件加速
- 可为 Linux 用户添加 VA-API/NVENC 等硬件加速支持检测
