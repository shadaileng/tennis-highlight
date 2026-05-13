"""
球拍检测模块 - 使用 YOLO 模型检测球拍位置

通过检测球拍位置，结合球的轨迹，可以识别击球时刻。
"""

import gzip
import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str, Optional[float]], None]

RACKET_MODEL_PATH = Path(__file__).parent.parent.parent / "models" / "racket.pt"

_racket_model = None


def _get_racket_model():
    """获取球拍检测模型 (懒加载)"""
    global _racket_model
    if _racket_model is None:
        from ultralytics import YOLO

        if not RACKET_MODEL_PATH.exists():
            logger.warning(f"Racket model not found: {RACKET_MODEL_PATH}")
            return None

        logger.info(f"Loading racket detection model from {RACKET_MODEL_PATH}")
        _racket_model = YOLO(str(RACKET_MODEL_PATH))

    return _racket_model


def detect_racket(frame: np.ndarray, model) -> Optional[dict]:
    """
    检测单帧中的球拍

    返回:
        {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "conf": conf} 或 None
    """
    results = model.predict(frame, conf=0.25, verbose=False)[0]

    if results.boxes is not None and len(results.boxes) > 0:
        # 返回置信度最高的球拍检测
        best_idx = np.argmax(results.boxes.conf.cpu().numpy())
        box = results.boxes.xyxy[best_idx].cpu().numpy()
        conf = float(results.boxes.conf[best_idx].cpu().numpy())

        return {
            "x1": float(box[0]),
            "y1": float(box[1]),
            "x2": float(box[2]),
            "y2": float(box[3]),
            "conf": conf,
            "cx": float((box[0] + box[2]) / 2),
            "cy": float((box[1] + box[3]) / 2),
        }

    return None


def find_hit_moments(ball_detections: list, racket_detections: list, video_fps: float) -> list[dict]:
    """
    通过球和球拍的位置关系，识别击球时刻

    击球检测逻辑:
    1. 球拍和球的距离突然变小（接近）
    2. 随后球的速度/方向发生改变

    返回:
        [{"frame_idx": idx, "time_s": t, "ball_pos": (x, y), ...}, ...]
    """
    hit_moments = []

    if not ball_detections or not racket_detections:
        return hit_moments

    # 计算每一帧球和球拍的距离
    distances = []
    for i in range(min(len(ball_detections), len(racket_detections))):
        ball_dict = ball_detections[i]
        racket = racket_detections[i]

        if 1 in ball_dict and racket is not None:
            x1, y1, x2, y2 = ball_dict[1]
            ball_cx = (x1 + x2) / 2
            ball_cy = (y1 + y2) / 2

            racket_cx = racket["cx"]
            racket_cy = racket["cy"]

            dist = np.sqrt((ball_cx - racket_cx)**2 + (ball_cy - racket_cy)**2)
            distances.append(dist)
        else:
            distances.append(float('inf'))

    # 检测距离突变（击球时刻）
    window = 5  # 检测窗口
    for i in range(window, len(distances) - window):
        # 当前距离
        curr_dist = distances[i]

        # 前后平均距离
        prev_avg = np.mean(distances[i-window:i])
        next_avg = np.mean(distances[i+1:i+window+1])

        # 击球特征: 距离先变小（接近），然后球加速离开
        if curr_dist < prev_avg * 0.5 and next_avg > curr_dist * 1.5:
            hit_moments.append({
                "frame_idx": i,
                "time_s": i / video_fps,
                "ball_pos": _get_ball_pos(ball_detections[i]),
            })

    return hit_moments


def _get_ball_pos(ball_dict: dict) -> Optional[tuple]:
    """获取球的位置中心"""
    if 1 in ball_dict:
        x1, y1, x2, y2 = ball_dict[1]
        return ((x1 + x2) / 2, (y1 + y2) / 2)
    return None
