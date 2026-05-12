"""
批量推理加速基准测试
对比逐帧 model.track() 与 批量 predict() + IoU 跟踪 的性能
"""
import time
import logging
from pathlib import Path

import cv2

logging.basicConfig(level=logging.WARNING)

from app.pipeline.ball_tracking import (
    _get_ball_model, _batch_detect, _reset_batch_tracker,
    _detect_frame, BATCH_SIZE
)

video_path = Path("data/uploads/acb378840d7015bd23faa08dd82d1e61_raw.mp4")
model = _get_ball_model()

cap = cv2.VideoCapture(str(video_path))
fps = cap.get(cv2.CAP_PROP_FPS)
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

# 测试 10 秒 = 300 帧
test_duration = 10.0
test_frames = int(test_duration * fps)
if test_frames > total:
    test_frames = total

print(f"测试 {test_frames} 帧 ({test_frames/fps:.1f}s @ {fps:.1f}fps)")
print(f"Batch size: {BATCH_SIZE}")
print(f"分辨率: 1920x{h}")
print()

# 读取所有测试帧
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

# ---- 方法1: 逐帧 model.track() ----
print("--- 方法1: 逐帧 model.track() ---")
t1 = time.monotonic()
single_results = []
for i, frame in enumerate(frames):
    ball_dict = _detect_frame(frame, model)
    single_results.append(ball_dict)
    if (i + 1) % 60 == 0:
        elapsed = time.monotonic() - t1
        print(f"  帧 {i+1}/{actual_frames}, {elapsed:.1f}s, {(i+1)/elapsed:.1f} fps")
t1_elapsed = time.monotonic() - t1
print(f"  总耗时: {t1_elapsed:.2f}s ({actual_frames/t1_elapsed:.1f} fps)")

single_valid = sum(1 for d in single_results if len(d) > 0)
single_ids = set()
for d in single_results:
    single_ids.update(d.keys())
print(f"  检测帧: {single_valid}/{actual_frames}, track IDs: {sorted(single_ids)}")
print()

# ---- 方法2: 批量 predict() + IoU 跟踪 ----
# 模拟主循环中的逐批处理方式
print("--- 方法2: 批量 predict() + IoU 跟踪 ---")
_reset_batch_tracker()
t2 = time.monotonic()
batch_results = []
processed = 0
while processed < actual_frames:
    batch_frames = frames[processed:processed + BATCH_SIZE]
    if not batch_frames:
        break
    result = _batch_detect(batch_frames, model, (h, 1920))
    batch_results.extend(result)
    processed += len(batch_frames)
    if processed % 60 == 0:
        elapsed = time.monotonic() - t2
        print(f"  帧 {processed}/{actual_frames}, {elapsed:.1f}s, {(processed)/elapsed:.1f} fps")
t2_elapsed = time.monotonic() - t2
print(f"  总耗时: {t2_elapsed:.2f}s ({actual_frames/t2_elapsed:.1f} fps)")

batch_valid = sum(1 for d in batch_results if len(d) > 0)
batch_ids = set()
for d in batch_results:
    batch_ids.update(d.keys())
print(f"  检测帧: {batch_valid}/{actual_frames}, track IDs: {sorted(batch_ids)}")
print()

# ---- 加速比 ----
speedup = t1_elapsed / t2_elapsed if t2_elapsed > 0 else float("inf")
print(f"=== 加速比: {speedup:.2f}x ===")
print(f"   逐帧:   {actual_frames/t1_elapsed:.1f} fps ({t1_elapsed:.1f}s)")
print(f"   批量:   {actual_frames/t2_elapsed:.1f} fps ({t2_elapsed:.1f}s)")
