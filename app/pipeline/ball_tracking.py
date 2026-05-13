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
BALL_ARTIFACT_VERSION = 6  # 增加 artifact 版本：新增球员检测与球速/跑动速度

# 帧跳跃检测参数（CPU 环境：每 N 帧检测一帧，其余帧通过插值补全）
DETECTION_STEP = 3  # 每 3 帧检测 1 帧，~3x 加速，由 _interpolate_ball_positions 补全

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
    results = model.track(frame, persist=True, conf=0.15, verbose=False)[0]

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


def _filter_stationary_tracks(ball_detections: list[dict[int, list[float]]], min_movement_px: float = 15.0) -> list[dict[int, list[float]]]:
    """
    过滤掉几乎不动的误检 track（背景固定位置的误检）

    真正的网球在视频中应该有明显位移。
    固定位置的误检通常是球场边线、地面标记等。
    """
    if not ball_detections:
        return ball_detections

    # 收集所有 track_id
    all_track_ids: set[int] = set()
    for det in ball_detections:
        all_track_ids.update(det.keys())

    tracks_to_keep: set[int] = set()
    for tid in all_track_ids:
        # 找出该 track 的第一次和最后一次出现
        first_pos = None
        last_pos = None
        for det in ball_detections:
            if tid in det:
                bbox = det[tid]
                cx = (bbox[0] + bbox[2]) / 2
                cy = (bbox[1] + bbox[3]) / 2
                if first_pos is None:
                    first_pos = (cx, cy)
                last_pos = (cx, cy)

        if first_pos is None or last_pos is None:
            continue

        # 计算总位移
        dx = last_pos[0] - first_pos[0]
        dy = last_pos[1] - first_pos[1]
        total_displacement = (dx * dx + dy * dy) ** 0.5

        if total_displacement >= min_movement_px:
            tracks_to_keep.add(tid)
        else:
            logger.info(f"Filtered stationary track {tid}: displacement={total_displacement:.1f}px < {min_movement_px}px (false positive)")

    # 至少保留一个 track
    if not tracks_to_keep and all_track_ids:
        logger.warning("All tracks filtered as stationary, keeping the one with most frames")
        track_frame_counts = {tid: sum(1 for d in ball_detections if tid in d) for tid in all_track_ids}
        tracks_to_keep.add(max(track_frame_counts, key=track_frame_counts.get))

    # 过滤
    result: list[dict[int, list[float]]] = []
    for det in ball_detections:
        filtered = {tid: bbox for tid, bbox in det.items() if tid in tracks_to_keep}
        result.append(filtered)

    return result


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


def _calculate_speed_events(
    hit_moments: list[dict],
    player_detections: list[dict[int, list[float]]],
    ball_mini_coords: list[dict[int, tuple]],
    player_mini_coords: list[dict[int, tuple]],
    video_fps: float,
    mini_court_width: int,
) -> dict:
    """
    计算球速和球员跑动速度（仅靠近镜头一侧的球员）

    算法: 相邻击球帧之间，计算球/球员在小场地上的位移 → 米 / 时间 → m/s
    参考 Tennis_AI_mainland/tennis_analysis/main.py

    靠近镜头球员判断：在画面中 foot y 坐标更大的球员（更靠画面下方）。

    Args:
        hit_moments: 击球时刻列表 [{"frame_idx": int, ...}]
        player_detections: 逐帧球员检测框 (原始像素坐标)
        ball_mini_coords: 逐帧球在小场地上的坐标
        player_mini_coords: 逐帧球员在小场地上的坐标
        video_fps: 视频帧率
        mini_court_width: 小场地绘制宽度（像素）

    Returns:
        {
            "ball_speeds": [{frame_idx, speed_ms, player_bbox}, ...],
            "player_speeds": [{start_frame, end_frame, speed_ms, start_pos, end_pos}, ...]
        }
    """
    from app.pipeline.mini_court import DOUBLE_LINE_WIDTH

    # 确定靠近镜头一侧的球员
    near_player_id = _find_near_side_player(player_detections)
    if near_player_id is None:
        logger.warning("Could not determine near-side player, skipping speed calculation")
        return {"ball_speeds": [], "player_speeds": []}

    ball_speeds = []
    player_speeds = []

    hit_frames = [h["frame_idx"] for h in hit_moments]

    for i in range(len(hit_frames) - 1):
        start_frame = hit_frames[i]
        end_frame = hit_frames[i + 1]
        duration_s = (end_frame - start_frame) / video_fps

        if duration_s <= 0:
            continue

        # 球速：相邻击球帧间球位移 → m/s
        ball_start = ball_mini_coords[start_frame].get(1) if start_frame < len(ball_mini_coords) else None
        ball_end = ball_mini_coords[end_frame].get(1) if end_frame < len(ball_mini_coords) else None

        if ball_start and ball_end:
            dist_px = _measure_distance(ball_start, ball_end)
            dist_m = (dist_px * DOUBLE_LINE_WIDTH) / mini_court_width
            ball_speed_ms = dist_m / duration_s

            # 判断击球球员
            player_positions = player_mini_coords[start_frame] if start_frame < len(player_mini_coords) else {}
            hitter_id = None
            if player_positions:
                hitter_id = min(
                    player_positions.keys(),
                    key=lambda pid: _measure_distance(
                        player_positions[pid], ball_start
                    ),
                )

                # 仅记录靠近镜头球员的球速
                if hitter_id == near_player_id:
                    hitter_bbox = []
                    if start_frame < len(player_detections) and hitter_id in player_detections[start_frame]:
                        hitter_bbox = player_detections[start_frame][hitter_id]

                    ball_speeds.append({
                        "frame_idx": start_frame,
                        "speed_ms": round(ball_speed_ms, 1),
                        "hitter_id": hitter_id,
                        "player_bbox": hitter_bbox,
                        "ball_pos": list(hit_moments[i].get("ball_pos", (0, 0))),
                    })

                # 跑动速度：仅计算靠近镜头球员的跑动
                opponent_id = next(
                    (pid for pid in player_positions if pid != hitter_id),
                    None,
                )
                if opponent_id is not None and opponent_id == near_player_id:
                    opp_start = player_mini_coords[start_frame].get(opponent_id)
                    opp_end = player_mini_coords[end_frame].get(opponent_id) if end_frame < len(player_mini_coords) else None

                    if opp_start and opp_end:
                        opp_dist_px = _measure_distance(opp_start, opp_end)
                        opp_dist_m = (opp_dist_px * DOUBLE_LINE_WIDTH) / mini_court_width
                        opp_speed_ms = opp_dist_m / duration_s

                        start_foot_pos = None
                        end_foot_pos = None
                        if start_frame < len(player_detections) and opponent_id in player_detections[start_frame]:
                            bbox = player_detections[start_frame][opponent_id]
                            start_foot_pos = ((bbox[0] + bbox[2]) / 2, bbox[3])
                        if end_frame < len(player_detections) and opponent_id in player_detections[end_frame]:
                            bbox = player_detections[end_frame][opponent_id]
                            end_foot_pos = ((bbox[0] + bbox[2]) / 2, bbox[3])

                        player_speeds.append({
                            "start_frame": start_frame,
                            "end_frame": end_frame,
                            "speed_ms": round(opp_speed_ms, 1),
                            "player_id": opponent_id,
                            "start_pos": start_foot_pos,
                            "end_pos": end_foot_pos,
                        })

    logger.info(
        f"Speed calculation (near-side player {near_player_id}): "
        f"{len(ball_speeds)} ball speed events, "
        f"{len(player_speeds)} player speed events"
    )
    return {
        "ball_speeds": ball_speeds,
        "player_speeds": player_speeds,
    }


def _find_near_side_player(player_detections: list[dict[int, list[float]]]) -> int | None:
    """
    确定靠近镜头一侧的球员（foot y 坐标更大的球员 = 更靠画面下方）
    通常在固定三脚架拍摄的网球视频中，近端球员在画面底部。
    """
    for det in player_detections:
        if len(det) >= 2:
            # 取两个球员中 foot y 更大的（更靠近镜头）
            player_ids = list(det.keys())
            foot_y_positions = []
            for pid in player_ids:
                bbox = det[pid]
                foot_y = bbox[3]  # y2 = 脚部位置
                foot_y_positions.append((pid, foot_y))

            foot_y_positions.sort(key=lambda x: x[1], reverse=True)
            near_id = foot_y_positions[0][0]
            logger.info(f"Near-side player: track_id={near_id} (foot_y={foot_y_positions[0][1]:.0f})")
            return near_id

    return None


def _measure_distance(p1, p2):
    """计算两点间欧几里得距离"""
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5


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

    # 逐帧检测 + 帧跳跃加速（CPU 环境：每 DETECTION_STEP 帧只检测 1 帧）
    # 未检测的帧通过 _interpolate_ball_positions 线性插值补全
    ball_detections: list[dict[int, list[float]]] = []
    player_detections: list[dict[int, list[float]]] = []
    racket_detections: list[dict | None] = []
    frame_idx = 0
    started = time.monotonic()

    # 尝试加载球拍检测模型
    racket_model = None
    try:
        from app.pipeline.racket_tracking import _get_racket_model, detect_racket
        racket_model = _get_racket_model()
        if racket_model:
            logger.info("Racket detection model loaded")
    except Exception as e:
        logger.warning(f"Racket model not available: {e}")

    # 尝试加载球员检测模型
    player_tracker = None
    try:
        from app.pipeline.player_tracker import PlayerTracker
        player_tracker = PlayerTracker()
        logger.info("Player tracker loaded")
    except Exception as e:
        logger.warning(f"Player tracker not available: {e}")

    while frame_idx < num_target_frames:
        ret, frame = cap.read()
        if not ret:
            break

        # 帧跳跃：每 DETECTION_STEP 帧检测一次，其余帧留空由插值补全
        if frame_idx % DETECTION_STEP == 0:
            ball_dict = _detect_frame(frame, model)
        else:
            ball_dict = {}
        ball_detections.append(ball_dict)

        # 球员检测（逐帧检测以保证跟踪连续性）
        if player_tracker:
            player_dict = player_tracker.detect_frame(frame)
        else:
            player_dict = {}
        player_detections.append(player_dict)

        # 球拍检测同样跳跃（与球检测同步）
        if racket_model:
            if frame_idx % DETECTION_STEP == 0:
                racket = detect_racket(frame, racket_model)
            else:
                racket = None
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
                progress_cb(min(85.0, pct), f"Detected {frame_idx}/{num_target_frames} (step={DETECTION_STEP})", eta)

    cap.release()

    # 插值填补缺失的检测 (参考 Tennis_AI_mainland)
    interpolated_detections = _interpolate_ball_positions(ball_detections)

    # 过滤几乎不动的误检 track（固定位置误检，如球场边线等）
    interpolated_detections = _filter_stationary_tracks(interpolated_detections, min_movement_px=15.0)

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

    # --- 新增：球员过滤 + 小场地映射 + 速度计算 ---
    court_keypoints_list = None
    speed_events = None
    filtered_player_detections = player_detections
    player_mini_coords = []
    ball_mini_coords = []

    if player_tracker and any(player_detections):
        progress_cb and progress_cb(85.0, "Detecting court lines...", None)
        try:
            from app.pipeline.court_line_detector import CourtLineDetector
            from app.pipeline.mini_court import MiniCourt

            # 读取第一帧用于场地检测
            cap2 = cv2.VideoCapture(str(video_path))
            cap2.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            ret, first_frame = cap2.read()
            cap2.release()

            if ret:
                court_detector = CourtLineDetector()
                court_keypoints = court_detector.predict(first_frame)
                court_keypoints_list = court_keypoints.tolist()

                # 过滤球员（只保留离场地最近的 2 名）
                filtered_player_detections = player_tracker.choose_and_filter_players(
                    court_keypoints, player_detections
                )

                # 小场地映射
                progress_cb and progress_cb(87.0, "Mapping to mini court...", None)
                mini_court = MiniCourt(first_frame)
                player_mini_coords, ball_mini_coords = mini_court.convert_bounding_boxes_to_mini_court_coordinates(
                    filtered_player_detections, interpolated_detections, court_keypoints_list
                )

                # 计算速度
                progress_cb and progress_cb(88.0, "Calculating speeds...", None)
                speed_events = _calculate_speed_events(
                    hit_moments,
                    filtered_player_detections,
                    ball_mini_coords,
                    player_mini_coords,
                    video_fps,
                    mini_court.get_width_of_mini_court(),
                )

        except Exception as e:
            logger.warning(f"Player/speed analysis failed (non-fatal): {e}")
            import traceback
            logger.debug(traceback.format_exc())

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

    # 新增字段（仅在成功时添加）
    if court_keypoints_list is not None:
        artifact["court_keypoints"] = court_keypoints_list
    if filtered_player_detections and any(filtered_player_detections):
        artifact["player_detections"] = filtered_player_detections
    if speed_events is not None:
        artifact["speed_events"] = speed_events

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
            player_detections=filtered_player_detections if filtered_player_detections and any(filtered_player_detections) else None,
            speed_events=speed_events,
        )
        logger.info(f"Ball trajectory video saved to: {video_output_path}")

    except Exception as e:
        logger.warning(f"Failed to generate ball trajectory video: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        video_output_path = None

    if progress_cb:
        progress_cb(100.0, f"Done: {valid_count} ball detections", None)

    return artifact_path, artifact, video_output_path


def load_ball_artifact(artifact_path: Path) -> dict:
    """加载 ball artifact"""
    with gzip.open(artifact_path, "rt", encoding="utf-8") as f:
        return json.load(f)
