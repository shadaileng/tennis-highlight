# 开发日志 (CHANGELOG)

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
