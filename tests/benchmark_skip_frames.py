"""
性能对比基准测试
对比 全帧检测 与 帧跳跃检测+插值 的性能
"""
import time
import logging
from pathlib import Path

import cv2

logging.basicConfig(level=logging.WARNING)

from app.pipeline.ball_tracking import (
    _get_ball_model, _detect_frame, _interpolate_ball_positions,
    _filter_stationary_tracks, DETECTION_STEP
)

video_path = Path("data/uploads/acb378840d7015bd23faa08dd82d1e61_raw.mp4")
model = _get_ball_model()

cap = cv2.VideoCapture(str(video_path))
fps = cap.get(cv2.CAP_PROP_FPS)
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

# 测试 N 帧
test_frames = 300
if test_frames > total:
    test_frames = total

print(f"测试 {test_frames} 帧 ({test_frames/fps:.1f}s @ {fps:.1f}fps)")
print(f"帧跳跃步长: {DETECTION_STEP}（每 {DETECTION_STEP} 帧检测 1 帧）")
print()

# 读取所有帧
cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
frames = []
for _ in range(test_frames):
    ret, f = cap.read()
    if not ret:
        break
    frames.append(f)
cap.release()
actual_frames = len(frames)
print(f"读取 {actual_frames} 帧")

# ---- 方法1: 逐帧全部检测 ----
print("--- 方法1: 逐帧全部检测 (原始方案) ---")
t1 = time.monotonic()
dets1 = []
for i, frame in enumerate(frames):
    d = _detect_frame(frame, model)
    dets1.append(d)
    if (i + 1) % 60 == 0:
        print(f"  帧 {i+1}/{actual_frames}, {time.monotonic()-t1:.1f}s")
t1_elapsed = time.monotonic() - t1
valid1 = sum(1 for d in dets1 if len(d) > 0)
ids1 = set()
for d in dets1:
    ids1.update(d.keys())
print(f"  总耗时: {t1_elapsed:.2f}s ({actual_frames/t1_elapsed:.1f} fps)")
print(f"  检测帧: {valid1}/{actual_frames}, track IDs: {sorted(ids1)[:10]}{'...' if len(ids1) > 10 else ''}")
print()

# ---- 方法2: 帧跳跃检测 (每N帧检测1帧) ----
print(f"--- 方法2: 帧跳跃检测 (每{DETECTION_STEP}帧检测1帧) ---")
t2 = time.monotonic()
dets2 = []
for i, frame in enumerate(frames):
    if i % DETECTION_STEP == 0:
        d = _detect_frame(frame, model)
    else:
        d = {}
    dets2.append(d)
    if (i + 1) % 60 == 0:
        print(f"  帧 {i+1}/{actual_frames}, {time.monotonic()-t2:.1f}s")
t2_elapsed = time.monotonic() - t2

# 插值补全缺失帧
dets2_interp = _interpolate_ball_positions(dets2)
# 过滤误检
dets2_interp = _filter_stationary_tracks(dets2_interp, min_movement_px=15.0)

valid2 = sum(1 for d in dets2 if len(d) > 0)
valid2_interp = sum(1 for d in dets2_interp if len(d) > 0)
ids2 = set()
for d in dets2_interp:
    ids2.update(d.keys())
print(f"  检测耗时: {t2_elapsed:.2f}s ({actual_frames/t2_elapsed:.1f} fps)")
print(f"  插值前检测帧: {valid2}/{actual_frames}")
print(f"  插值后检测帧: {valid2_interp}/{actual_frames}")
print(f"  track IDs: {sorted(ids2)[:10]}{'...' if len(ids2) > 10 else ''}")
print()

# ---- 加速比 ----
speedup = t1_elapsed / t2_elapsed
print(f"=== 加速比: {speedup:.2f}x ===")
print(f"   全帧:   {actual_frames/t1_elapsed:.1f} fps ({t1_elapsed:.1f}s)")
print(f"   跳跃:   {actual_frames/t2_elapsed:.1f} fps ({t2_elapsed:.1f}s)")
