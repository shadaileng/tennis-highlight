"""
网球检测模块 - 参考 Tennis_AI_mainland 项目实现

直接使用 YOLO 模型在原始视频帧上检测网球，返回边界框列表。
参考: https://github.com/qchen34/Tennis_AI_mainland
"""

import gzip
import json
import logging
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str, float | None], None]

BALL_ALGORITHM = "ball_tracking_yolo"
BALL_ARTIFACT_VERSION = 4

BALL_MODEL_PATH = Path(__file__).parent.parent.parent / "models" / "yolo5_last.pt"

_ball_model = None


def _get_ball_model():
    """获取 YOLO 网球检测模型 (懒加载)"""
    global _ball_model
    if _ball_model is None:
        from ultralytics import YOLO

        if not BALL_MODEL_PATH.exists():
            raise RuntimeError(
                f"Ball model not found: {BALL_MODEL_PATH}\n"
                f"Please download from: https://drive.google.com/file/d/1UZwiG1jkWgce9lNhxJ2L0NVjX1vGM05U/view"
            )

        logger.info(f"Loading ball detection model from {BALL_MODEL_PATH}")
        _ball_model = YOLO(str(BALL_MODEL_PATH))

    return _ball_model


def _detect_frame(frame: np.ndarray, model) -> dict[int, list[float]]:
    """
    检测单帧中的网球

    返回:
        {1: [x1, y1, x2, y2]} 格式的边界框，track_id=1 固定
        如果没有检测到，返回空字典 {}
    """
    results = model.predict(frame, conf=0.15, verbose=False)[0]

    ball_dict = {}
    if results.boxes is not None and len(results.boxes) > 0:
        for box in results.boxes:
            result = box.xyxy.tolist()[0]
            ball_dict[1] = result

    return ball_dict


def _interpolate_ball_positions(ball_detections: list[dict[int, list[float]]]) -> list[dict[int, list[float]]]:
    """
    插值填补缺失的网球位置
    参考 Tennis_AI_mainland/tennis_analysis/trackers/ball_tracker.py
    """
    if not ball_detections:
        return ball_detections

    # 提取 [x1, y1, x2, y2] 列表
    positions = [x.get(1, []) for x in ball_detections]

    # 转换为 DataFrame
    df = pd.DataFrame(positions, columns=["x1", "y1", "x2", "y2"])

    # 检查是否有有效数据
    if df.empty or df[["x1", "y1", "x2", "y2"]].isna().all().all():
        return ball_detections

    # 线性插值
    df = df.interpolate()
    df = df.bfill()

    # 转回原格式
    return [{1: x} for x in df.to_numpy().tolist()]


def analyze_ball_trajectory(
    analysis_id: str,
    video_path: Path,
    video_duration_s: float,
    sample_fps: float,
    range_start_s: float,
    range_end_s: float,
    progress_cb: ProgressCallback | None = None,
) -> tuple[Path, dict, Path | None]:
    """
    使用 YOLO 模型检测网球并生成轨迹数据

    返回:
        (artifact_path, artifact, video_output_path)
    """
    from app.config import settings
    from app.pipeline.video_renderer import draw_ball_trajectory_video

    model = _get_ball_model()

    if progress_cb:
        progress_cb(1.0, "Loading ball model...", None)

    # 打开视频
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 跳到起始位置
    start_frame = int(range_start_s * video_fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    duration = range_end_s - range_start_s
    # 实际要检测的帧数
    num_target_frames = int(duration * video_fps)

    if progress_cb:
        progress_cb(5.0, f"Detecting balls in {num_target_frames} frames", None)

    # 逐帧检测 (只检测指定范围内的帧)
    ball_detections: list[dict[int, list[float]]] = []
    racket_detections: list[dict | None] = []  # 新增：球拍检测
    frame_idx = 0
    started = time.monotonic()

    # 尝试加载球拍检测模型
    racket_model = None
    try:
        from app.pipeline.racket_tracking import _get_racket_model
        racket_model = _get_racket_model()
        if racket_model:
            logger.info("Racket detection model loaded")
    except Exception as e:
        logger.warning(f"Racket model not available: {e}")

    while frame_idx < num_target_frames:
        ret, frame = cap.read()
        if not ret:
            break

        # 每帧都检测球
        ball_dict = _detect_frame(frame, model)
        ball_detections.append(ball_dict)

        # 检测球拍（如果模型可用）
        if racket_model:
            from app.pipeline.racket_tracking import detect_racket
            racket = detect_racket(frame, racket_model)
            racket_detections.append(racket)
        else:
            racket_detections.append(None)

        frame_idx += 1

        if frame_idx % 30 == 0:
            elapsed = time.monotonic() - started
            rate = frame_idx / elapsed if elapsed > 0 else 0
            remaining = max(0, num_target_frames - frame_idx)
            eta = remaining / rate if rate > 0 else None
            pct = 5.0 + (frame_idx / num_target_frames) * 80.0 if num_target_frames > 0 else 85.0
            if progress_cb:
                progress_cb(min(85.0, pct), f"Detected {frame_idx}/{num_target_frames}", eta)

    cap.release()

    # 插值填补缺失的检测 (参考 Tennis_AI_mainland)
    interpolated_detections = _interpolate_ball_positions(ball_detections)

    # 检测击球时刻
    hit_moments = []
    if racket_model and any(r is not None for r in racket_detections):
        from app.pipeline.racket_tracking import find_hit_moments
        hit_moments = find_hit_moments(interpolated_detections, racket_detections, video_fps)
        logger.info(f"Found {len(hit_moments)} hit moments")

    # 计算检测统计
    valid_count = sum(1 for d in interpolated_detections if 1 in d)
    detection_rate = (valid_count / len(interpolated_detections) * 100) if interpolated_detections else 0

    metadata = {
        "detector": BALL_ALGORITHM,
        "detector_version": BALL_ARTIFACT_VERSION,
        "model_path": str(BALL_MODEL_PATH),
        "video_width": width,
        "video_height": height,
        "target_width": width,  # 目标尺寸 = 视频原始尺寸
        "target_height": height,
        "video_fps": video_fps,
        "duration_s": float(video_duration_s),
        "range_start_s": float(range_start_s),
        "range_end_s": float(range_end_s),
        "sample_fps": video_fps,  # 逐帧检测，sample_fps 等于视频帧率
        "median_bg_samples": 0,   # ball tracking 不需要此参数
        "sample_count": len(interpolated_detections),
    }

    summary = {
        "total_samples": len(interpolated_detections),
        "detected_frames": valid_count,
        "detection_rate": detection_rate,
        "avg_confidence": 0.5,  # 插值后的点使用默认置信度
    }

    artifact = {
        "metadata": metadata,
        "summary": summary,
        "ball_detections": interpolated_detections,  # 与 Tennis_AI_mainland 格式一致
        "hit_moments": hit_moments,  # 击球时刻列表
    }

    # 保存 artifact
    settings.analysis_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"
    with gzip.open(artifact_path, "wt", encoding="utf-8") as f:
        json.dump(artifact, f)

    # 生成带轨迹的视频
    video_output_path = None
    try:
        if progress_cb:
            progress_cb(90.0, "Generating video with ball trajectory...", None)

        settings.ball_videos_dir.mkdir(parents=True, exist_ok=True)
        video_output_path = settings.ball_videos_dir / f"{analysis_id}_ball_trajectory.mp4"

        draw_ball_trajectory_video(
            video_path=video_path,
            ball_detections=interpolated_detections,
            output_path=video_output_path,
            range_start_s=range_start_s,
            sample_fps=sample_fps,
            progress_cb=progress_cb,
        )
        logger.info(f"Ball trajectory video saved to: {video_output_path}")

    except Exception as e:
        logger.warning(f"Failed to generate ball trajectory video: {e}")
        video_output_path = None

    if progress_cb:
        progress_cb(100.0, f"Done: {valid_count} ball detections", None)

    return artifact_path, artifact, video_output_path


def load_ball_artifact(artifact_path: Path) -> dict:
    """加载 ball artifact"""
    with gzip.open(artifact_path, "rt", encoding="utf-8") as f:
        return json.load(f)
