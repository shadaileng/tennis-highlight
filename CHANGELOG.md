# 开发日志 (CHANGELOG)

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
