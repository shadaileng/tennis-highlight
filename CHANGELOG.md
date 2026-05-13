# 开发日志 (CHANGELOG)

## 2026-05-13 11:04:00 (UTC+8)

### feat: 跑动速度连线起点到终点延伸动画

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/video_renderer.py` | `_draw_speed_overlays`: 跑动连线分两阶段动画（前60%时间延伸，后40%渐淡）；延伸端带移动圆点 |

### 详细说明

1. 分两阶段动画：前60%持续时间连线从起点向终点逐渐延伸，后40%完整连线渐淡消失
2. 速度文本始终显示在已画出线段的中间位置
3. 延伸阶段在端点处画黄色实心圆+白色外圈，增强运动感

---

## 2026-05-13 11:03:00 (UTC+8)

### fix: ball_tracking_yolo 类型点击 Open 播放渲染视频

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `static/app.js` | `openAnalysis`: 检测 ball_tracking_yolo 时设置 `ballAnalysisId`；`loadEditor`: 使用渲染视频 URL 替代原始视频 |

### 详细说明

1. `openAnalysis` 中判断 `a.algorithm === "ball_tracking_yolo"` 时标记 `state.ballAnalysisId`
2. `loadEditor` 对有 `ballAnalysisId` 的分析，设置 videoUrl 为 `/analysis/{id}/ball-video`（渲染轨迹+球速的视频）
3. 非 ball_tracking_yolo 类型行为不变，仍使用原始视频

---

## 2026-05-13 11:02:00 (UTC+8)

### fix: 球速/跑动速度改为 m/s，仅计算靠近镜头一侧球员

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/ball_tracking.py` | `_calculate_speed_events`: 移除 `*3.6` 单位改为 m/s；新增 `_find_near_side_player` 仅输出靠近镜头球员的球速和跑动速度 |
| `app/pipeline/video_renderer.py` | `speed_kmh` → `speed_ms`，文本标签 `km/h` → `m/s` |
| `AGENT.md` | 更新速度事件说明，新增靠近镜头球员逻辑 |

### 详细说明

1. 单位变更：`ball_speeds[].speed_kmh` / `player_speeds[].speed_kmh` → `speed_ms`，移除 `×3.6` 换算
2. 新增 `_find_near_side_player`：取第一帧中 foot y 坐标更大的球员（画面下方=靠近三脚架镜头）作为目标球员
3. 球速：仅当该球员为击球者时记录
4. 跑动速度：仅当该球员为非击球方时记录

---

## 2026-05-13 11:00:00 (UTC+8)

### feat: 新增球员检测、小场地映射与球速/跑动速度计算

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/court_line_detector.py` | 新建：ResNet50 场地14关键点检测 |
| `app/pipeline/player_tracker.py` | 新建：YOLO 球员检测与过滤 |
| `app/pipeline/mini_court.py` | 新建：小场地坐标系映射，像素↔米转换 |
| `app/pipeline/ball_tracking.py` | 集成球员检测+小场地映射+球速/跑动速度计算 |
| `app/pipeline/video_renderer.py` | 新增球速文本渲染和跑动连线渲染 |
| `AGENT.md` | 同步更新目录结构、artifact 格式、速度事件说明 |

### 详细说明

1. **新增3个模块**：
   - `court_line_detector.py`：加载 `keypoints_model.pth`（ResNet50），预测14个场地关键点坐标
   - `player_tracker.py`：使用 YOLOv8 逐帧检测球员，自动过滤保留距场地最近的2人
   - `mini_court.py`：建立250x500的小场地坐标系，利用球员身高作比例尺实现像素↔米转换

2. **球速计算**（`_calculate_speed_events`）：
   - 相邻击球帧间，球在小场地上的位移 × `DOUBLE_LINE_WIDTH`(10.97m) / 小场地宽度 / 时间 × 3.6
   - 击球球员判断：击球帧时离球最近的球员

3. **跑动速度计算**：
   - 同一段击球间隔内，对手（非击球方）在小场地上的位移，公式同球速

4. **渲染效果**：
   - 球速：击球后在球员右上方显示青色渐淡文本 "XXX km/h"，持续0.8秒
   - 跑动连线：起点→终点画黄色半透明连线，中点显示 "XXX km/h"，持续1.0秒

5. **失败安全**：球员检测/场地检测失败时不影响原有球检测和轨迹渲染功能

---

### fix: 删除视频时未清理 data/analysis 和 data/ball_videos 下的残留文件

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/database.py` | `delete_video_cascade` 返回新增 `artifacts` 和 `ball_videos` 路径列表 |
| `app/routers/videos.py` | 删除路由将 `artifacts` 和 `ball_videos` 中的文件一并删除 |

### 详细说明

1. 做了什么：删除视频时同时清理 `data/analysis/` 下的分析 artifacts（`.ball.json.gz`, `.pose.json.gz`, `.npz`）和 `data/ball_videos/` 下的轨迹视频（`{analysis_id}_ball_trajectory.mp4`）
2. 怎么做的：
   - `delete_video_cascade._q()` 中，在删除 analyses 记录前先查询 `artifact_path` 和 `video_path` 字段，收集后一并返回
   - `delete_video` 路由将它们加入遍历删除的路径列表
3. 预期效果：删除视频后不再残留无关的分析文件和轨迹视频文件

---

## 2026-05-13 10:51:00 (UTC+8)

### fix: delete_video_cascade 漏删 analyses 导致外键约束失败

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/database.py` | `delete_video_cascade` 中删除 videos 前增加 `DELETE FROM analyses` |

### 详细说明

1. 做了什么：`DELETE /videos/{id}` 时因 `analyses` 表仍有外键引用 videos 记录，报 `FOREIGN KEY constraint failed`
2. 怎么做的：在 `delete_video_cascade` 的 `_q()` 中先删 `segments`，再删 `analyses`，再删 `exports`，最后删 `videos`
3. 预期效果：删除视频时，所有关联的分析记录也被清除，不再报外键错误

---

## 2026-05-13 10:45:00 (UTC+8)

### fix: 启动脚本端口被占用时无法正确停止旧进程

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `scripts/restart.sh` | stop_service 增加 `fuser -k 8000/tcp` 端口级清理；start_service 增加端口检测+"Uvicorn running on" 启动确认 |

### 详细说明

1. 做了什么：修复 `restart.sh` 在端口被旧进程占用时无法正确停止并重启动的问题
2. 怎么做的：
   - `stop_service`：增加 `fuser -k 8000/tcp` 作为兜底（比 `pkill` 更可靠，直接杀端口）
   - `start_service`：启动前先用 `fuser` 检查 8000 端口；启动后用轮循等待日志中出现 `Uvicorn running on`，最长 10s，超时则报错退出
3. 预期效果：无论旧进程如何启动，`restart` 都能先清理端口再启动，不再出现 `address already in use`

---

## 2026-05-13 10:40:00 (UTC+8)

### fix: orchestrator.py lambda 闭包无法被 pickle 导致 ProcessPoolExecutor 报错

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/orchestrator.py` | 将 3 处 `lambda` 闭包替换为可 pickle 的 `functools.partial` + 模块级别函数 |

### 详细说明

1. 做了什么：将 `run_analysis` 中所有匿名 lambda 闭包替换为模块级别的可 pickle 函数，解决 `ProcessPoolExecutor` pickling 失败问题
2. 怎么做的：
   - 新增模块级别函数 `_progress_cb`（3 参数+ETA）和 `_progress_cb_no_eta`（2 参数，用于 motion_analysis）
   - 删除局部嵌套函数 `progress_cb`
   - 将 `lambda: f(...)` 改为 `partial(f, ..., partial(_progress_cb, analysis_id), ...)`
   - 导入 `functools.partial`
3. 预期效果：后台上传分析使用 `ProcessPoolExecutor` 时不再报 `Can't pickle local object` 错误

---

## 2026-05-13 10:20:00 (UTC+8)

### fix: ball.py 后台任务同步阻塞导致页面卡死

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/routers/ball.py` | 两个后台 `run()` 函数中 `analyze_ball_trajectory` 改为 `loop.run_in_executor` 异步执行，并传入 `progress_cb` 更新进度 |

### 详细说明

1. 之前的问题：`start_ball_analysis` 和 `start_ball_analysis_from_video` 的后台任务直接同步调用 `analyze_ball_trajectory`（CPU 密集型 YOLO 推理），**阻塞了主事件循环**
   - 所有并发 API 请求（`GET /media`、`GET /analyses`、`GET /static/app.js` 等）被阻塞
   - 前端轮询/刷新全部卡死，直到 45 秒后台任务完成
   - `progress_cb=None` 导致数据库进度始终为 0%

2. 修复：
   - import `asyncio` 和 `update_analysis_progress_sync`
   - 使用 `loop.run_in_executor(None, lambda: ...)` 将 CPU 密集型调用移出事件循环
   - 提供 `progress_cb` 定期写入进度到数据库，前端 `pollBallAnalysis` 可读取到实时进度

3. 效果：POST 返回后事件循环立即空闲，前端轮询/刷新正常进行，进度条实时更新

---

## 2026-05-13 10:00:00 (UTC+8)

### fix: 点击 Start Analysis 后分析列表立即新增记录并实时更新进度

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `static/app.js` | 修复 `renderAnalyses` 的 section 可见性逻辑、优化 `startAnalysis`/`startBallTrajectoryAnalysis` 即时追加 pending 记录、`pollBallAnalysis` 每次轮询更新 UI |

### 详细说明

1. 之前的问题：点击 Start Analysis 后分析列表不立即出现新记录，`pollBallAnalysis` 轮询时不更新进度条
2. 修复了 4 个 bug：
   - `renderAnalyses` 中 `analyses-section` 的 `hidden` 逻辑从 toggle 翻转改为 `!analyses.length`
   - `startAnalysis` POST 成功后立即构造 pending 记录推入 `state.analyses` 并渲染
   - `startBallTrajectoryAnalysis` 同上，立即追加 pending 记录
   - `pollBallAnalysis` 每次轮询后调用 `renderAnalyses(analyses)` 更新进度
3. 效果：点击后表格立即出现 "pending" 记录，2 秒后进度条和状态实时更新

---

## 2026-05-13 09:50:00 (UTC+8)

### chore: 新增测试资源参数化规则 — 禁止硬编码文件路径

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `.codebuddy/rules/test-resource-params.mdc` | 新建：测试资源文件必须通过参数传入，禁止硬编码 |
| `.codebuddy/rules/sync-agent-md.mdc` | 补充 `test-resource-params` 子规则为触发条件 |
| `AGENT.md` | 测试规范中增加「资源文件必须参数化」约定 |

### 详细说明

1. 动机：当前 5 个测试文件（`test_ball_tracking.py`、`test_ball_trajectory_e2e.py` 等）硬编码了 `input_video.mp4` 路径，无法在其他环境直接复用
2. 规则要求：
   - 脚本式测试使用 `argparse` 接收资源路径
   - unittest 类测试通过参数传入，不存在的文件使用 `self.skipTest`
   - 允许 `_DEFAULT_VIDEO` 仅作为 `__main__` 块的 fallback，且必须有命令行参数覆盖
3. 同步将 `AGENT.md` 中的测试规范更新为包含此约定

---

## 2026-05-12 16:00:00 (UTC+8)

### chore: 清理 _batch_detect 相关代码 — IoU 跟踪在 CPU 环境质量差

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/ball_tracking.py` | 删除 `_bbox_iou`、`_reset_batch_tracker`、`_batch_detect` 函数及全局跟踪器状态；删除 `BATCH_IOU_THRESHOLD` 常量 |
| `app/pipeline/racket_tracking.py` | 删除 `_batch_racket_detect` 批量球拍检测函数 |
| `tests/benchmark_batch_inference.py` | 删除（依赖已移除的 `_batch_detect`） |
| `tests/benchmark_skip_frames.py` | 新增命令行参数支持：`<video_path> [--duration N]` |

### 详细说明

1. 原因：`_batch_detect` 使用 `model.predict()` + IoU 贪心匹配替代 `model.track()`，在 CPU 环境下：
   - 加速仅 1.11x（批次推理在 CPU 上无实质收益）
   - track ID 从 3 个暴增至 65 个（IoU 对小目标高速球无效）
2. 当前最佳方案：保留 `model.track()` + `DETECTION_STEP=3` 帧跳跃，实测 3.1x 加速且跟踪稳定
3. 基准测试脚本 `benchmark_skip_frames.py` 增加 `argparse` 支持命令行传参

---

## 2026-05-12 15:45:00 (UTC+8)

### feat: 帧跳跃检测加速 — CPU 环境 3x 加速

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/ball_tracking.py` | 新增 `DETECTION_STEP=3` 帧跳跃常量；主循环每 3 帧仅检测 1 帧，其余由插值补全；保留 `_batch_detect` + IoU 跟踪作为 GPU 环境可选方案 |
| `app/pipeline/ball_tracking.py` | 新增 `_bbox_iou` 工具函数 |
| `app/pipeline/racket_tracking.py` | 新增 `_batch_racket_detect` 批量球拍检测函数（供 GPU 环境使用） |

### 详细说明

1. 做了什么：将逐帧检测改为每 3 帧检测 1 帧的帧跳跃策略，加速视频检测流程
2. 怎么做的：
   - 主循环中 `frame_idx % DETECTION_STEP == 0` 时才调用 `_detect_frame(frame, model)`
   - 跳跃帧设为空字典 `{}`，由 `_interpolate_ball_positions` 线性插值补全
   - 保留 `_batch_detect` 函数（含 IoU 贪心跟踪 + 全局 track ID 计数器），供 GPU 环境使用
   - 留空 `_batch_racket_detect` 供 GPU 环境使用
3. 基准测试结果（300 帧，CPU）：
   - 全帧检测：61.5s (4.9 fps)，148/300 帧有检测
   - 帧跳跃：20.5s (14.6 fps)，**3.0x 加速**
   - 插值后：300/300 帧全部补全
   - Track ID：跳跃方案更稳定（1 个 ID vs 全帧 4 个 ID）
4. 发现：当前环境无 CUDA（`torch.cuda.is_available()=False`），批量推理在 CPU 上无加速效果

---

## 2026-05-12 15:00:00 (UTC+8)

### fix: conf 从 0.25 降回 0.15 — 低置信度网球不再漏检

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/ball_tracking.py` | `conf=0.25` → `conf=0.15` |

### 详细说明

1. 原因：`acb378...` 视频中真实网球置信度仅 0.16~0.27，`conf=0.25` 导致大部分帧漏检，插值后的轨迹不准确
2. 降回 `conf=0.15` 后：两个测试视频均正确检测到 1 个真实网球
   - `input_video.mp4`：Ball 1（60/60 帧，位移 392px）✓
   - `acb378...`：Ball 2（60/60 帧，位移 442px）✓
3. 由 `_filter_stationary_tracks` + 尺寸过滤共同把关误检

---

## 2026-05-12 14:50:00 (UTC+8)

### fix: 新增运动量过滤 — 移除背景固定位置的误检

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/ball_tracking.py` | 新增 `_filter_stationary_tracks()`，过滤总位移 <15px 的 track |

### 详细说明

1. 问题：Ball 48（681,352）和 Ball 53（682,228）在整段视频中几乎不移动，是球场背景固定位置的误检
2. 解决：新增 `_filter_stationary_tracks()` 函数，计算每个 track 首尾位置的总位移，过滤掉位移 <15px 的静止 track（球场边线、地面标记等）
3. 测试结果：`input_video.mp4` 从 3 个 track 降为 1 个（真实网球），检测率仍为 100%

---

## 2026-05-12 14:40:00 (UTC+8)

### feat: 多球测试脚本默认改为全时长检测

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `tests/test_multi_ball_tracking.py` | `--duration` 默认值从 3.0 改为 `None`（全时长） |

### 详细说明

1. 修改 `--duration` 参数默认值从 `3.0` 变为 `None`
2. 未指定 `--duration` 时，自动使用视频全时长进行检测
3. 通过 `args.duration or duration_s` 回退到全时长，`min(..., duration_s)` 确保不超过视频长度

---

## 2026-05-12 14:35:00 (UTC+8)

### fix: 降低网球误检率 — 提高置信度阈值 + 尺寸过滤

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/ball_tracking.py` | 新增 `_is_valid_ball_bbox` 尺寸过滤器；`conf` 从 0.15 提升至 0.25 |

### 详细说明

1. 问题：`conf=0.15` 过低，背景物体被误检为网球（如截图中 track_id 48、53），且跟踪丢失后重新检测分配新 ID
2. 解决：
   - 置信度阈值从 **0.15 提升至 0.25**，减少低置信度误检
   - 新增 `_is_valid_ball_bbox()` 尺寸过滤器：排除 <5px 噪声、>画面 18% 的大物体、异常宽高比的检测框
3. 测试：单元验证通过，合理网球框不会被过滤

---

## 2026-05-12 14:25:00 (UTC+8)

### feat: video_renderer 使用 ffmpeg pipe 直接编码 H.264

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/video_renderer.py` | 用 ffmpeg stdin pipe 替换 cv2.VideoWriter |

### 详细说明

1. 替换 cv2.VideoWriter（mp4v 编码器，浏览器兼容性差）为 ffmpeg pipe 直接编码 H.264
2. 通过 subprocess.Popen 启动 ffmpeg，逐帧将 OpenCV 渲染结果（BGR24 raw 格式）写入 stdin
3. ffmpeg 参数：libx264 编码、yuv420p 像素格式（浏览器兼容）、preset fast、crf 23
4. 预期效果：生成的 MP4 文件可直接在浏览器中播放，无需额外转码

---

## 2026-05-12 14:08:00 (UTC+8)

### docs: AGENT.md + 项目规则 — 自动同步与开发完成流程

修改文件：
| 文件 | 修改内容 |
|------|----------|
| `AGENT.md` | 顶部添加同步说明；Git 工作流增加收尾流程规范及 CHANGELOG 格式示例 |
| `.codebuddy/rules/sync-agent-md.mdc` | 新建：项目变更时自动同步 AGENT.md 的触发规则 |
| `.codebuddy/rules/dev-complete-flow.mdc` | 新建：开发完成后写入 CHANGELOG → 提交 Git 的标准化流程 |

### 详细说明

1. 新增 `dev-complete-flow` 项目规则：每次完成修改后按「确认目标 → 更新 CHANGELOG → Git 提交 → 同步 AGENT.md」顺序执行
2. 新增 `sync-agent-md` 项目规则：指定哪些文件变更需要同步更新 AGENT.md，以及对应章节映射
3. AGENT.md 更新：顶部添加 `⚠️ 注意` 提醒，Git 工作流部分增加完整收尾流程描述和 CHANGELOG 格式示例

---

## 2026-05-12 10:02:00 (UTC+8)

### 优化：网球轨迹样式 - 白色→橙黄色平滑曲线

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/video_renderer.py` | 轨迹颜色/长度/平滑度优化，移除球体标记 |

**连续修改记录：**

| # | 修改 | 说明 |
|---|------|------|
| 1 | 颜色：青绿色 → 橙色→淡黄色渐变 | 参考专业转播样式 |
| 2 | 宽度：渐变粗细 → 等宽曲线 | 每层固定厚度 3/5/7 |
| 3 | 长度：25帧 → 10帧 → 7个网球长度 | 控制点从 25 → 15 → 7 |
| 4 | 平滑：直线段 → **Catmull-Rom 样条插值** | 控制点间插值 8 个子点，消除锯齿 |
| 5 | 渐变色：橙色→淡黄 → 白色→橙黄色 | 末端纯白，球端橙黄 BGR(30,200,255) |
| 6 | 球体：移除发光标记 | 只有轨迹，不单独标记球位 |

**核心代码变更：**

```python
# Catmull-Rom 样条插值（新增）
def _catmull_rom_chunk(p0, p1, p2, p3, num_pts=8):
    """4个控制点平滑插值 p1→p2，生成 num_pts 个点"""
    for i in range(num_pts):
        t = i / (num_pts - 1)
        t2 = t * t; t3 = t2 * t
        x = 0.5 * (2*p1[0] + (-p0[0]+p2[0])*t + ... + (-p0[0]+3*p1[0]-3*p2[0]+p3[0])*t3)
        y = 0.5 * (2*p1[1] + (-p0[1]+p2[1])*t + ... + (-p0[1]+3*p1[1]-3*p2[1]+p3[1])*t3)

# 轨迹绘制（修改）
trail_length = min(7, len(trajectory_points))       # 7个网球长度
smooth_pts = []
for i in range(len(raw_pts) - 1):
    chunk = _catmull_rom_chunk(p0, p1, p2, p3, 8)    # 平滑插值
    smooth_pts.extend(chunk)

for layer in range(3):                               # 三层等宽发光
    for i in range(1, len(smooth_pts)):
        t = i / (len(smooth_pts) - 1)
        b = int(255 - 225*t)  # 白色→橙黄
        g = int(255 - 55*t)
        r = 255
        cv2.line(overlay, smooth_pts[i-1], smooth_pts[i], (b,g,r), 3+layer*2)
```

---

## 2026-05-12 09:44:54 (UTC+8)

### 新增：击球瞬间 - 细长菱形飞出效果

**功能描述：**
将击球火花效果从粒子系统改为"细长菱形扩散飞出"效果，模拟击球瞬间的能量爆发。

**效果特征：**
1. 每个击球点随机生成 **15-25个细长菱形**
2. 菱形**角度/大小/速度完全随机**，不规则扩散
3. **近端宽（钝角朝向中心）→ 远端尖（锐角指向外）**
4. 从击球点**向外飞出 + 逐渐缩小 + 逐渐变淡**
5. **纯白色开始 → 逐帧渐淡 → 完全消失**，共持续 0.5 秒
6. 菱形宽度/长度比仅 0.08~0.2（极细长）

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/video_renderer.py` | 完全重写火花系统为细长菱形扩散效果 |
| `app/pipeline/ball_tracking.py` | 修复 `draw_ball_trajectory_video()` 未传入 `hit_moments` 参数的问题 |

**核心代码变更：**

```python
# video_renderer.py - 菱形生成（第24-67行）
def _init_sparks(hit_moments, video_fps):
    for hit in hit_moments:
        for _ in range(random.randint(15, 25)):
            diamonds.append({
                "angle": random.uniform(0, 2*pi),
                "speed": random.uniform(10, 25),
                "length": random.uniform(35, 70),
                "width_ratio": random.uniform(0.08, 0.2),
            })

# 菱形顶点计算 - 近端宽远端尖
def _get_diamond_vertices(cx, cy, direction_angle, distance, length, width_ratio):
    far_extent = length * 0.65    # 远端尖点
    near_extent = length * 0.35   # 近端基点（钝角）
    half_width = length * width_ratio * 0.5
    # 近端角度 = 2*arctan(0.5*width_ratio / 0.15) ≈ 53°
    # 远端角度 = 2*arctan(0.5*width_ratio / 0.65) ≈ 14°
    return [near_base, left, far_tip, right]

# 绘制三层效果：外光晕 + 半透明填充 + 核心亮边
def _draw_diamond(frame, ...):
    cv2.fillPoly(overlay, [glow_arr], color)  # 外发光
    cv2.addWeighted(alpha * 0.12)
    cv2.fillPoly(overlay, [pts], color)        # 主菱形
    cv2.addWeighted(alpha * 0.4)
    cv2.polylines(overlay, [pts], True, white) # 亮边
    cv2.addWeighted(alpha * 0.7)
```

```python
# ball_tracking.py - 修复第365行
# 修改前：draw_ball_trajectory_video(...)  # 缺少hit_moments
# 修改后：draw_ball_trajectory_video(..., hit_moments=hit_moments)
```

**视觉演变记录：**
1. 🔴 圆形粒子散射（橙黄色，不明显）
2. 🔴 放射性线条（均匀分布，太单调）
3. 🔴 锯齿闪电分支（效果怪异）
4. 🟢 **细长菱形扩散飞出（当前效果）**

---

## 2026-05-12 (UTC+8)

### 优化：整合 Tennis_AI_mainland 击球检测算法

**功能描述：**
从 `Tennis_AI_mainland` 项目移植基于轨迹分析的击球检测算法，作为球拍检测的补充方案。

**算法原理（Tennis_AI_mainland）：**
1. 计算球中心的 Y 坐标
2. 使用滚动平均（window=5）平滑轨迹
3. 计算 `delta_y`（Y 方向的速度变化）
4. 检测方向反转：当 `delta_y` 从正变负或从负变正时，可能是击球点
5. 如果在连续 ~25 帧内方向变化一致，则判定为击球

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/ball_tracking.py` | 添加 `get_ball_shot_frames()` 函数（从 Tennis_AI_mainland 移植） |
| `app/pipeline/ball_tracking.py` | 添加 `_convert_to_hit_moments()` 辅助函数 |
| `app/pipeline/ball_tracking.py` | 修改 `analyze_ball_trajectory()` 整合两种击球检测方法 |

**核心代码变更：**

```python
# ball_tracking.py - 新增函数（从 Tennis_AI_mainland 移植）
def get_ball_shot_frames(ball_detections: list[dict]) -> list[int]:
    """
    基于球的轨迹方向变化检测击球时刻
    返回: 击球时刻的帧索引列表
    """
    df['mid_y'] = (df['y1'] + df['y2']) / 2
    df['mid_y_rolling_mean'] = df['mid_y'].rolling(window=5, min_periods=1).mean()
    df['delta_y'] = df['mid_y_rolling_mean'].diff()
    
    # 检测方向反转
    for i in range(1, len(df) - int(minimum_change_frames_for_hit * 1.2)):
        negative_position_change = df['delta_y'].iloc[i] > 0 and df['delta_y'].iloc[i + 1] < 0
        positive_position_change = df['delta_y'].iloc[i] < 0 and df['delta_y'].iloc[i + 1] > 0
        # ... 验证持续帧数 ...
```

```python
# ball_tracking.py - 修改 analyze_ball_trajectory()
# 整合两种击球检测方法
hit_moments = []

# 方法1: 基于球拍检测 (如果可用)
if racket_model and any(r is not None for r in racket_detections):
    racket_hit_moments = find_hit_by_racket(...)
    hit_moments.extend(racket_hit_moments)

# 方法2: 基于轨迹分析 (从 Tennis_AI_mainland 移植)
trajectory_hit_frames = get_ball_shot_frames(interpolated_detections)
if trajectory_hit_frames:
    trajectory_hit_moments = _convert_to_hit_moments(...)
    hit_moments.extend(trajectory_hit_moments)

# 去重 (基于帧索引)
seen_frames = set()
unique_hit_moments = []
for moment in hit_moments:
    if moment['frame_idx'] not in seen_frames:
        seen_frames.add(moment['frame_idx'])
        unique_hit_moments.append(moment)
```

**优势：**
- 即使球拍检测模型不可用，也能通过轨迹分析检测击球
- 两种方法互为补充，提高检测准确率
- 自动去重，避免重复检测

**测试：**
- 创建 `test_integrated_hit_detection.py` 测试脚本
- 测试通过，无语法错误

---

## 2026-05-12 (UTC+8)

### 新增：球拍检测 + 击球火花效果

**功能描述：**
1. 使用 YOLO 模型检测球拍位置
2. 通过球和球拍的位置关系识别击球时刻
3. 在击球时刻添加火花粒子效果

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/racket_tracking.py` | 新增球拍检测模块 |
| `app/pipeline/ball_tracking.py` | 添加球拍检测和击球时刻识别 |
| `app/pipeline/video_renderer.py` | 添加火花粒子效果渲染 |

**核心代码变更：**

```python
# racket_tracking.py - 球拍检测
def detect_racket(frame, model) -> dict:
    # 返回球拍位置 {"x1", "y1", "x2", "y2", "conf"}

def find_hit_moments(ball_detections, racket_detections, video_fps) -> list:
    # 检测击球时刻：球拍和球距离突变
    # 1. 距离突然变小（接近）
    # 2. 随后球加速离开
```

```python
# video_renderer.py - 火花粒子效果
def _init_sparks(hit_moments, video_fps):
    # 为每个击球时刻创建50个火花粒子
    # 粒子具有随机速度、大小和颜色

def _draw_sparks(frame, current_frame_idx):
    # 绘制火花粒子（多层发光效果）
    # 粒子受重力影响，透明度随生命周期衰减
```

**使用说明：**
1. 将球拍检测模型放置到 `models/racket.pt`
2. 运行球轨迹分析，自动检测击球时刻
3. 生成的视频将在击球时刻显示火花效果

**效果：**
- 球拍检测（如果模型可用）
- 击球时刻自动识别
- 火花粒子效果（50个粒子/击球，0.2-0.5秒生命周期）

---

## 2026-05-12 (UTC+8)

### 新增：视频轨迹渲染样式 - 青绿色发光拖尾效果

**功能描述：**
后台生成的网球轨迹视频使用青绿色发光拖尾效果，参考专业网球转播视觉效果。

**修改文件：**

| 文件 | 修改内容 |
|------|----------|
| `app/pipeline/video_renderer.py` | 重写 `draw_ball_trajectory_video()` 绘制逻辑 |

**核心代码变更：**

```python
# 绘制发光拖尾效果 (青绿色发光拖尾)
if len(trajectory_points) > 1:
    trail_length = min(15, len(trajectory_points))
    start_idx = len(trajectory_points) - trail_length
    pts = trajectory_points[start_idx:]

    # 三层发光效果（不同透明度）
    for layer in range(3):
        alpha_base = 0.3 + layer * 0.25  # 0.3, 0.55, 0.8
        
        for i in range(1, len(pts)):
            progress = i / (len(pts) - 1)  # 0.0 -> 1.0
            # 青绿色渐变
            r = int(50 * progress)
            g = int(200 + 55 * progress)
            b = int(180 + 75 * progress)
            
            overlay = frame.copy()
            cv2.line(overlay, pts[i - 1], pts[i], (b, g, r), thickness)
            cv2.addWeighted(overlay, alpha_base * progress, frame, ...)

# 绘制发光球体（三层光晕 + 白色核心）
for radius, alpha in [(20, 0.2), (14, 0.4), (10, 0.6)]:
    overlay = frame.copy()
    cv2.circle(overlay, (cx, cy), radius, (180, 230, 100), -1)
    cv2.addWeighted(overlay, alpha, frame, ...)
```

**效果：**
- 移除黄色边界框
- 青绿色渐变拖尾（最近 15 帧）
- 三层发光球体效果
- 白色核心高光

---

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
