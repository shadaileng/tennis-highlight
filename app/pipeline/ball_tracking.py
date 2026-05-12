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


def _is_valid_ball_bbox(bbox: list[float], frame_shape) -> bool:
    """检查边界框是否为合理的网球

    过滤条件:
    1. 尺寸不能太大（网球在画面中很小）
    2. 宽高比不能太离谱（网球接近圆形→正方形）
    3. 尺寸不能太小（排除噪声）
    """
    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1
    frame_h, frame_w = frame_shape[:2]

    # 最小尺寸（太小的框是噪声）
    if w < 5 or h < 5:
        return False

    # 最大尺寸（网球不会超过画面宽/高的 1/6）
    max_ball_size = max(frame_w, frame_h) * 0.18
    if w > max_ball_size or h > max_ball_size:
        return False

    # 宽高比（网球检测框接近正方形）
    aspect_ratio = w / h if h > 0 else 0
    if aspect_ratio < 0.4 or aspect_ratio > 2.5:
        return False

    return True


def _detect_frame(frame: np.ndarray, model) -> dict[int, list[float]]:
    """
    检测单帧中的网球，支持多个球

    返回:
        {track_id: [x1, y1, x2, y2]} 格式的边界框，每个球有唯一 track_id
        如果没有检测到，返回空字典 {}
    """
    # 使用 track 模式代替 predict，获得稳定的跨帧跟踪 ID
    results = model.track(frame, persist=True, conf=0.25, verbose=False)[0]

    ball_dict = {}
    if results.boxes is not None and len(results.boxes) > 0:
        for box in results.boxes:
            bbox = box.xyxy.tolist()[0]
            # 尺寸过滤：排除明显不是网球的误检
            if not _is_valid_ball_bbox(bbox, frame.shape):
                continue
            # 使用跟踪 ID（如果有的话），否则用递增索引
            if box.id is not None:
                track_id = int(box.id.tolist()[0])
            else:
                track_id = len(ball_dict) + 1
            ball_dict[track_id] = bbox

    return ball_dict


def _interpolate_ball_positions(ball_detections: list[dict[int, list[float]]]) -> list[dict[int, list[float]]]:
    """
    插值填补缺失的网球位置，支持多个球各自独立插值
    参考 Tennis_AI_mainland/tennis_analysis/trackers/ball_tracker.py
    """
    if not ball_detections:
        return ball_detections

    # 收集所有 track_id
    all_track_ids: set[int] = set()
    for det in ball_detections:
        all_track_ids.update(det.keys())

    if not all_track_ids:
        return ball_detections

    num_frames = len(ball_detections)

    # 提取每个 track_id 的轨迹（缺失为 None/空列表）
    per_track_data: dict[int, list[list[float]]] = {}
    for tid in all_track_ids:
        positions: list[list[float]] = []
        for det in ball_detections:
            positions.append(det.get(tid, []))
        per_track_data[tid] = positions

    # 对每个 track_id 独立插值
    interpolated_per_track: dict[int, list[list[float]]] = {}
    for tid in all_track_ids:
        positions = per_track_data[tid]
        df = pd.DataFrame(positions, columns=["x1", "y1", "x2", "y2"])
        if not df.empty and not df[["x1", "y1", "x2", "y2"]].isna().all().all():
            df = df.interpolate()
            df = df.bfill()
            interpolated_per_track[tid] = df.to_numpy().tolist()
        else:
            interpolated_per_track[tid] = positions

    # 重新组合每帧
    result: list[dict[int, list[float]]] = []
    for frame_idx in range(num_frames):
        frame_dict: dict[int, list[float]] = {}
        for tid in all_track_ids:
            pos = interpolated_per_track[tid][frame_idx]
            # 跳过插值后仍然是 NaN 的位置
            if pos and not any(isinstance(p, float) and np.isnan(p) for p in pos):
                frame_dict[tid] = pos
        result.append(frame_dict)

    return result


def get_ball_shot_frames(ball_detections: list[dict[int, list[float]]]) -> list[int]:
    """
    基于球的轨迹方向变化检测击球时刻，支持多球
    参考 Tennis_AI_mainland/tennis_analysis/trackers/ball_tracker.py

    算法原理:
    1. 对每个球的独立轨迹分别分析
    2. 计算球中心的 Y 坐标
    3. 使用滚动平均平滑轨迹
    4. 计算 delta_y (Y 方向的速度变化)
    5. 检测方向反转: 当 delta_y 从正变负或从负变正时，可能是击球点
    6. 如果在连续 ~25 帧内方向变化一致，则判定为击球

    返回:
        击球时刻的帧索引列表（所有球的击球帧合并去重）
    """
    if not ball_detections:
        return []

    # 收集所有 track_id
    all_track_ids: set[int] = set()
    for det in ball_detections:
        all_track_ids.update(det.keys())

    if not all_track_ids:
        return []

    all_shot_frames: set[int] = set()

    for track_id in all_track_ids:
        # 提取该球的轨迹
        positions: list[list[float]] = [x.get(track_id, []) for x in ball_detections]

        # 转换为 DataFrame
        df = pd.DataFrame(positions, columns=['x1', 'y1', 'x2', 'y2'])

        # 如果没有有效数据，跳过
        if df.empty or df[['x1', 'y1', 'x2', 'y2']].isna().all().all():
            continue

        # 使用 .loc 避免链式赋值警告
        df = df.copy()
        df.loc[:, 'ball_hit'] = 0

        # 计算球中心的 Y 坐标
        df.loc[:, 'mid_y'] = (df['y1'] + df['y2']) / 2

        # 使用滚动平均平滑
        df.loc[:, 'mid_y_rolling_mean'] = df['mid_y'].rolling(window=5, min_periods=1, center=False).mean()

        # 计算 delta_y (Y 方向的速度变化)
        df.loc[:, 'delta_y'] = df['mid_y_rolling_mean'].diff()

        minimum_change_frames_for_hit = 25

        for i in range(1, len(df) - int(minimum_change_frames_for_hit * 1.2)):
            negative_position_change = df['delta_y'].iloc[i] > 0 and df['delta_y'].iloc[i + 1] < 0
            positive_position_change = df['delta_y'].iloc[i] < 0 and df['delta_y'].iloc[i + 1] > 0

            if negative_position_change or positive_position_change:
                change_count = 0
                for change_frame in range(i + 1, i + int(minimum_change_frames_for_hit * 1.2) + 1):
                    negative_position_change_following_frame = df['delta_y'].iloc[i] > 0 and df['delta_y'].iloc[change_frame] < 0
                    positive_position_change_following_frame = df['delta_y'].iloc[i] < 0 and df['delta_y'].iloc[change_frame] > 0

                    if negative_position_change and negative_position_change_following_frame:
                        change_count += 1
                    elif positive_position_change and positive_position_change_following_frame:
                        change_count += 1

                if change_count > minimum_change_frames_for_hit - 1:
                    df.loc[i, 'ball_hit'] = 1

        track_shot_frames = df[df['ball_hit'] == 1].index.tolist()
        all_shot_frames.update(track_shot_frames)
        logger.debug(f"Track {track_id}: found {len(track_shot_frames)} shot frames")

    return sorted(all_shot_frames)


def _convert_to_hit_moments(ball_detections: list[dict[int, list[float]]], 
                              shot_frames: list[int], 
                              video_fps: float) -> list[dict]:
    """
    将击球帧索引列表转换为 hit_moments 格式
    支持多球：击球时使用该帧中任意可用的球位置

    参数:
        ball_detections: 球检测列表
        shot_frames: 击球帧索引列表
        video_fps: 视频帧率

    返回:
        hit_moments 格式列表: [{"frame_idx": idx, "time_s": t, "ball_pos": (x, y)}, ...]
    """
    hit_moments = []

    for frame_idx in shot_frames:
        if frame_idx < len(ball_detections):
            ball_dict = ball_detections[frame_idx]
            if ball_dict:
                # 使用该帧中第一个检测到的球的位置
                first_tid = list(ball_dict.keys())[0]
                x1, y1, x2, y2 = ball_dict[first_tid]
                ball_pos = ((x1 + x2) / 2, (y1 + y2) / 2)

                hit_moments.append({
                    "frame_idx": frame_idx,
                    "time_s": frame_idx / video_fps,
                    "ball_pos": ball_pos,
                    "detection_method": "trajectory_analysis",
                })

    return hit_moments


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

    # 检测击球时刻 - 使用两种方法
    hit_moments = []
    
    # 方法1: 基于球拍检测 (如果可用)
    if racket_model and any(r is not None for r in racket_detections):
        from app.pipeline.racket_tracking import find_hit_moments as find_hit_by_racket
        racket_hit_moments = find_hit_by_racket(interpolated_detections, racket_detections, video_fps)
        hit_moments.extend(racket_hit_moments)
        logger.info(f"Found {len(racket_hit_moments)} hit moments by racket detection")
    
    # 方法2: 基于轨迹分析 (从 Tennis_AI_mainland 移植)
    trajectory_hit_frames = get_ball_shot_frames(interpolated_detections)
    if trajectory_hit_frames:
        trajectory_hit_moments = _convert_to_hit_moments(
            interpolated_detections, trajectory_hit_frames, video_fps
        )
        hit_moments.extend(trajectory_hit_moments)
        logger.info(f"Found {len(trajectory_hit_moments)} hit moments by trajectory analysis")
    
    # 去重 (基于帧索引)
    seen_frames = set()
    unique_hit_moments = []
    for moment in hit_moments:
        frame_idx = moment["frame_idx"]
        if frame_idx not in seen_frames:
            seen_frames.add(frame_idx)
            unique_hit_moments.append(moment)
    
    hit_moments = unique_hit_moments
    logger.info(f"Total unique hit moments: {len(hit_moments)}")

    # 计算检测统计
    valid_count = sum(1 for d in interpolated_detections if len(d) > 0)
    total_ball_instances = sum(len(d) for d in interpolated_detections)
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
        "total_ball_instances": total_ball_instances,
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
            hit_moments=hit_moments,
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
