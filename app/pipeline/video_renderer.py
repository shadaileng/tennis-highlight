"""
视频渲染模块 - 参考 Tennis_AI_mainland 项目实现

直接在原始视频帧上绘制网球轨迹和击球火花效果。
参考: https://github.com/qchen34/Tennis_AI_mainland
"""

import logging
import random
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str, float | None], None]

# 放射性光芒系统
_spark_effects: list[dict] = []


def _init_sparks(hit_moments: list[dict], video_fps: float):
    """初始化细长菱形飞出效果"""
    global _spark_effects
    _spark_effects = []

    for hit in hit_moments:
        frame_idx = hit.get("frame_idx", int(hit.get("time_s", 0) * video_fps))
        pos = hit.get("ball_pos", (0, 0))
        
        if not isinstance(pos, (list, tuple)) or len(pos) < 2:
            logger.warning(f"Invalid ball_pos for hit moment: {hit}")
            continue

        # 创建15-25个细长菱形
        num_diamonds = random.randint(15, 25)
        diamonds = []
        
        for _ in range(num_diamonds):
            angle = random.uniform(0, 2 * np.pi)
            speed = random.uniform(10, 25)
            # 菱形总长度（较大 = 细长）
            length = random.uniform(35, 70)
            # 宽度比（非常小 = 极细长）
            width_ratio = random.uniform(0.08, 0.2)
            
            diamonds.append({
                "angle": angle,
                "speed": speed,
                "length": length,
                "width_ratio": width_ratio,
            })

        _spark_effects.append({
            "frame_start": frame_idx,
            "duration_frames": int(0.5 * video_fps),
            "x": pos[0],
            "y": pos[1],
            "diamonds": diamonds,
        })
    
    logger.info(f"Initialized {len(_spark_effects)} thin diamond effects for {len(hit_moments)} hit moments")


def _draw_sparks(frame: np.ndarray, current_frame_idx: int) -> np.ndarray:
    """在当前帧绘制细长菱形飞出效果"""
    global _spark_effects
    
    if not _spark_effects:
        return frame
    
    h, w = frame.shape[:2]

    for effect in _spark_effects:
        age = current_frame_idx - effect["frame_start"]

        if age < 0 or age > effect["duration_frames"]:
            continue

        progress = age / effect["duration_frames"]
        # 纯白开始 → 逐渐变淡
        alpha = max(0, 1.0 - progress ** 1.2)
        if alpha <= 0:
            continue
        
        cx, cy = int(effect["x"]), int(effect["y"])
        
        for diamond in effect["diamonds"]:
            # 飞行距离
            distance = diamond["speed"] * age
            # 菱形逐渐变小（1.2倍起始 → 缩到0）
            size_scale = 1.2 - progress * 1.2
            if size_scale <= 0:
                continue
            
            _draw_diamond(
                frame,
                cx, cy,
                diamond["angle"],
                distance,
                diamond["length"] * size_scale,
                diamond["width_ratio"],
                alpha,
            )
        
        # 中心高亮光晕（纯白，淡出）
        if alpha > 0.2:
            for radius, r_alpha in [(10, 0.25 * alpha), (5, 0.4 * alpha)]:
                overlay = frame.copy()
                cv2.circle(overlay, (cx, cy), radius, (255, 255, 255), -1)
                cv2.addWeighted(overlay, r_alpha, frame, 1 - r_alpha, 0, frame)

    return frame


def _get_diamond_vertices(
    cx: int, cy: int,
    direction_angle: float,
    distance: float,
    length: float,
    width_ratio: float,
) -> list[tuple[int, int]]:
    """
    计算细长菱形的4个顶点
    
    形状: 近端宽（钝角），远端尖（锐角）
    - 菱形总长度 = length
    - 最宽处宽度 = length * width_ratio (非常细)
    - 最宽处靠近近端 (偏离中心向后 0.3*length)
    - 尖点距离中心向前 0.7*length
    """
    if length < 3:
        return []
    
    cos_dir = np.cos(direction_angle)
    sin_dir = np.sin(direction_angle)
    perp_cos = np.cos(direction_angle + np.pi / 2)
    perp_sin = np.sin(direction_angle + np.pi / 2)
    
    # 菱形中心（从击球点飞出一定距离）
    center_x = cx + cos_dir * distance
    center_y = cy + sin_dir * distance
    
    half_width = length * width_ratio * 0.5
    
    # 沿方向: 远端尖点(+), 近端基点(-)
    far_extent = length * 0.65    # 远端尖点（锐角）
    near_offset = length * 0.2    # 最宽处偏移（在近端一侧）
    near_extent = length * 0.35   # 近端基点（钝角）
    
    # 4个顶点
    far_tip = (
        int(center_x + cos_dir * far_extent),
        int(center_y + sin_dir * far_extent),
    )
    near_base = (
        int(center_x - cos_dir * near_extent),
        int(center_y - sin_dir * near_extent),
    )
    right = (
        int(center_x - cos_dir * near_offset + perp_cos * half_width),
        int(center_y - sin_dir * near_offset + perp_sin * half_width),
    )
    left = (
        int(center_x - cos_dir * near_offset - perp_cos * half_width),
        int(center_y - sin_dir * near_offset - perp_sin * half_width),
    )
    
    return [near_base, left, far_tip, right]


def _draw_diamond(
    frame: np.ndarray,
    cx: int, cy: int,
    direction_angle: float,
    distance: float,
    length: float,
    width_ratio: float,
    alpha: float,
):
    """绘制单个细长菱形（纯白 → 渐淡）"""
    vertices = _get_diamond_vertices(cx, cy, direction_angle, distance, length, width_ratio)
    
    if len(vertices) < 4 or alpha <= 0.01:
        return
    
    pts = np.array(vertices, dtype=np.int32).reshape((-1, 1, 2))
    
    # 纯白到渐淡
    intensity = int(255 * min(1.0, alpha * 1.5))
    color = (intensity, intensity, intensity)
    
    # 外层光晕菱形（放大）
    glow_pts = []
    for v in vertices:
        gx = sum(p[0] for p in vertices) / 4
        gy = sum(p[1] for p in vertices) / 4
        glow_pts.append((
            int(v[0] + (v[0] - gx) * 1.8),
            int(v[1] + (v[1] - gy) * 1.8),
        ))
    glow_arr = np.array(glow_pts, dtype=np.int32).reshape((-1, 1, 2))
    
    overlay = frame.copy()
    cv2.fillPoly(overlay, [glow_arr], color)
    cv2.addWeighted(overlay, alpha * 0.12, frame, 1 - alpha * 0.12, 0, frame)
    
    # 主菱形（半透明填充）
    overlay = frame.copy()
    cv2.fillPoly(overlay, [pts], color)
    cv2.addWeighted(overlay, alpha * 0.4, frame, 1 - alpha * 0.4, 0, frame)
    
    # 核心亮边（纯白）
    if alpha > 0.15:
        overlay = frame.copy()
        cv2.polylines(overlay, [pts], True, (255, 255, 255), 1)
        cv2.addWeighted(overlay, alpha * 0.7, frame, 1 - alpha * 0.7, 0, frame)


def draw_ball_trajectory_video(
    video_path: Path,
    ball_detections: list[dict[int, list[float]]],
    output_path: Path,
    range_start_s: float = 0.0,
    sample_fps: float = 4.0,
    progress_cb: ProgressCallback | None = None,
    hit_moments: list[dict] | None = None,
) -> Path:
    """
    在原始视频上绘制网球轨迹并输出 MP4

    参考 Tennis_AI_mainland/tennis_analysis/trackers/ball_tracker.py 的 draw_bboxes 方法

    Args:
        video_path: 输入视频路径
        ball_detections: 网球检测结果列表 [{1: [x1, y1, x2, y2]}, ...]
        output_path: 输出视频路径
        range_start_s: 起始时间（秒）
        sample_fps: 采样帧率
        progress_cb: 进度回调
        hit_moments: 击球时刻列表（用于火花效果）

    Returns:
        输出视频路径
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 打开视频
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 创建视频写入器
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(str(output_path), fourcc, video_fps, (width, height))

    if not out.isOpened():
        raise RuntimeError(f"Cannot create video writer for: {output_path}")

    logger.info(f"Rendering ball trajectory video: {width}x{height} @ {video_fps}fps, {total_frames} frames")

    # 跳到起始位置
    start_frame = int(range_start_s * video_fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    # 用于绘制轨迹
    trajectory_points: list[tuple[int, int]] = []

    # 初始化火花粒子系统
    if hit_moments:
        _init_sparks(hit_moments, video_fps)
        logger.info(f"Initialized spark effects for {len(hit_moments)} hit moments")

    try:
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # 绘制火花效果（在球之前）
            if hit_moments:
                frame = _draw_sparks(frame, frame_idx)

            # 逐帧绘制检测结果
            if frame_idx < len(ball_detections):
                ball_dict = ball_detections[frame_idx]
                if 1 in ball_dict:
                    x1, y1, x2, y2 = ball_dict[1]

                    # 计算中心点
                    cx = int((x1 + x2) / 2)
                    cy = int((y1 + y2) / 2)

                    # 添加到轨迹
                    trajectory_points.append((cx, cy))

                    # 绘制发光拖尾效果 (参考图片: 青绿色发光拖尾)
                    if len(trajectory_points) > 1:
                        # 使用最近15个点，创建渐隐拖尾
                        trail_length = min(15, len(trajectory_points))
                        start_idx = len(trajectory_points) - trail_length
                        pts = trajectory_points[start_idx:]

                        # 创建发光效果 - 多层绘制实现模糊发光
                        for layer in range(3):
                            alpha_base = 0.3 + layer * 0.25  # 0.3, 0.55, 0.8

                            for i in range(1, len(pts)):
                                progress = i / (len(pts) - 1)  # 0.0 -> 1.0
                                # 青绿色渐变: 头部亮青色，尾部暗青色
                                r = int(50 * progress)
                                g = int(200 + 55 * progress)
                                b = int(180 + 75 * progress)
                                alpha = alpha_base * progress

                                color = (b, g, r)  # OpenCV 使用 BGR
                                thickness = max(2, int((3 - layer) * progress * 3))

                                # 使用半透明叠加
                                overlay = frame.copy()
                                cv2.line(overlay, pts[i - 1], pts[i], color, thickness)
                                cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

                    # 绘制当前球位置 (发光球体)
                    # 外层光晕
                    for radius, alpha in [(20, 0.2), (14, 0.4), (10, 0.6)]:
                        overlay = frame.copy()
                        cv2.circle(overlay, (cx, cy), radius, (180, 230, 100), -1)
                        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

                    # 核心亮点
                    cv2.circle(frame, (cx, cy), 6, (200, 255, 220), -1)
                    cv2.circle(frame, (cx, cy), 4, (255, 255, 255), -1)

            # 写入帧
            out.write(frame)
            frame_idx += 1

            # 报告进度
            if progress_cb and frame_idx % 30 == 0:
                pct = (frame_idx / total_frames) * 100 if total_frames > 0 else 0
                progress_cb(min(95.0, pct + 5.0), f"Rendering frame {frame_idx}/{total_frames}", None)

    finally:
        cap.release()
        out.release()
        logger.info(f"Ball trajectory video saved to: {output_path}")

    return output_path


def render_ball_video_from_artifact(
    video_path: Path,
    artifact: dict,
    output_path: Path,
    progress_cb: ProgressCallback | None = None,
) -> Path:
    """
    从 artifact 数据渲染带网球轨迹的视频

    Args:
        video_path: 输入视频路径
        artifact: ball.json.gz 解析后的 artifact 数据
        output_path: 输出视频路径
        progress_cb: 进度回调

    Returns:
        输出视频路径
    """
    ball_detections = artifact.get("ball_detections", [])
    metadata = artifact.get("metadata", {})
    hit_moments = artifact.get("hit_moments", [])

    return draw_ball_trajectory_video(
        video_path=video_path,
        ball_detections=ball_detections,
        output_path=output_path,
        range_start_s=metadata.get("range_start_s", 0.0),
        sample_fps=metadata.get("sample_fps", 4.0),
        progress_cb=progress_cb,
        hit_moments=hit_moments if hit_moments else None,
    )
