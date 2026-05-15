"""
视频渲染模块 - 参考 Tennis_AI_mainland 项目实现

直接在原始视频帧上绘制网球轨迹和击球火花效果。
参考: https://github.com/qchen34/Tennis_AI_mainland
"""

import logging
import random
import subprocess
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str, float | None], None]


def _catmull_rom_chunk(p0, p1, p2, p3, num_pts: int = 8) -> list[tuple[int, int]]:
    """Catmull-Rom 样条插值 p1→p2，生成 num_pts 个平滑插值点"""
    pts = []
    for i in range(num_pts):
        t = i / (num_pts - 1) if num_pts > 1 else 0
        t2 = t * t
        t3 = t2 * t
        x = int(0.5 * (
            2 * p1[0] + (-p0[0] + p2[0]) * t +
            (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
            (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
        ))
        y = int(0.5 * (
            2 * p1[1] + (-p0[1] + p2[1]) * t +
            (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
            (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
        ))
        pts.append((x, y))
    return pts

# 放射性光芒系统
_spark_effects: list[dict] = []

# 球速/跑动速度叠加系统
_ball_speed_overlays: list[dict] = []
_player_speed_overlays: list[dict] = []

# 落地点标记系统
_landing_markers: list[dict] = []


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


def _init_speed_overlays(speed_events: dict | None, video_fps: float):
    """初始化球速和跑动速度叠加系统（仅初始化近端球员的数据）"""
    global _ball_speed_overlays, _player_speed_overlays
    _ball_speed_overlays = []
    _player_speed_overlays = []

    if not speed_events:
        return

    near_player_id = speed_events.get("near_player_id")

    # 球速显示：击球后击球位置右上方显示约 0.8 秒（仅近端球员）
    for bs in speed_events.get("ball_speeds", []):
        if near_player_id is not None and bs.get("hitter_id") != near_player_id:
            continue
        duration_frames = max(int(0.8 * video_fps), 1)
        _ball_speed_overlays.append({
            "frame_start": bs["frame_idx"],
            "duration_frames": duration_frames,
            "speed_ms": bs["speed_ms"],
            "ball_pos": bs.get("ball_pos"),
            "player_bbox": bs.get("player_bbox", []),
            "player_id": bs.get("hitter_id"),
        })

    # 跑动速度：在击球间隔内画连线，显示约 1.0 秒（仅近端球员）
    for ps in speed_events.get("player_speeds", []):
        if near_player_id is not None and ps.get("player_id") != near_player_id:
            continue
        duration_frames = max(int(1.0 * video_fps), 1)
        _player_speed_overlays.append({
            "frame_start": max(0, ps["end_frame"] - duration_frames),
            "duration_frames": duration_frames,
            "speed_ms": ps["speed_ms"],
            "start_pos": ps.get("start_pos"),
            "end_pos": ps.get("end_pos"),
            "player_id": ps.get("player_id"),
        })

    logger.info(
        f"Initialized {len(_ball_speed_overlays)} ball speed overlays (near player {near_player_id}), "
        f"{len(_player_speed_overlays)} player speed overlays"
    )


def _draw_speed_overlays(frame: np.ndarray, current_frame_idx: int) -> np.ndarray:
    """在当前帧绘制球速文本和跑动速度连线"""
    global _ball_speed_overlays, _player_speed_overlays

    h, w = frame.shape[:2]

    # 绘制跑动速度连线（在球速文本之下）
    for overlay in _player_speed_overlays:
        age = current_frame_idx - overlay["frame_start"]
        if age < 0 or age > overlay["duration_frames"]:
            continue

        total_duration = overlay["duration_frames"]
        progress = age / total_duration if total_duration > 0 else 1

        start_pos = overlay.get("start_pos")
        end_pos = overlay.get("end_pos")
        if not start_pos or not end_pos:
            continue

        p1 = (int(start_pos[0]), int(start_pos[1]))
        p2 = (int(end_pos[0]), int(end_pos[1]))

        # 确保点在画面内
        if not (0 <= p1[0] < w and 0 <= p1[1] < h and 0 <= p2[0] < w and 0 <= p2[1] < h):
            continue

        # 动画分两阶段：
        #   阶段1 (0-60% 时长): 连线从起点向终点延伸
        #   阶段2 (60-100% 时长): 完整连线，渐淡消失
        extend_phase = 0.6  # 前60%时间用于延伸
        extend_progress = min(1.0, progress / extend_phase) if extend_phase > 0 else 1.0
        alpha = max(0, 1.0 - (progress - extend_phase) / (1.0 - extend_phase)) if progress > extend_phase else 1.0

        # 根据延伸进度计算当前线段的终点
        if extend_progress < 1.0:
            current_end = (
                int(p1[0] + (p2[0] - p1[0]) * extend_progress),
                int(p1[1] + (p2[1] - p1[1]) * extend_progress),
            )
            # 显示文本在当前线段的中间位置
            mid_x = (p1[0] + current_end[0]) // 2
            mid_y = (p1[1] + current_end[1]) // 2
        else:
            current_end = p2
            mid_x = (p1[0] + p2[0]) // 2
            mid_y = (p1[1] + p2[1]) // 2

        if alpha <= 0:
            continue

        # 画连线（黄色半透明）
        overlay_img = frame.copy()
        cv2.line(overlay_img, p1, current_end, (0, 255, 255), 3)
        cv2.addWeighted(overlay_img, alpha * 0.6, frame, 1 - alpha * 0.6, 0, frame)

        # 在延伸端点画移动圆点（增加动画感）
        if extend_progress < 1.0:
            cv2.circle(frame, current_end, 5, (0, 255, 255), -1)
            cv2.circle(frame, current_end, 8, (255, 255, 255), 2)

        speed_text = f"P{overlay['player_id']} {overlay['speed_ms']} m/s"
        # 文字背景
        (tw, th), _ = cv2.getTextSize(speed_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        text_x = mid_x - tw // 2
        text_y = mid_y - 10
        cv2.rectangle(
            overlay_img,
            (text_x - 4, text_y - th - 4),
            (text_x + tw + 4, text_y + 4),
            (0, 0, 0),
            -1,
        )
        cv2.addWeighted(overlay_img, 0.5, frame, 0.5, 0, frame)

        # 速度文本（黄色）
        cv2.putText(
            frame, speed_text, (text_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2,
        )

    # 绘制击球球速文本（在击球位置右上方显示）
    for overlay in _ball_speed_overlays:
        age = current_frame_idx - overlay["frame_start"]
        if age < 0 or age > overlay["duration_frames"]:
            continue

        progress = age / overlay["duration_frames"] if overlay["duration_frames"] > 0 else 1
        alpha = max(0, 1.0 - progress)
        if alpha <= 0:
            continue

        ball_pos = overlay.get("ball_pos")
        if not ball_pos or len(ball_pos) < 2:
            continue

        bx, by = int(ball_pos[0]), int(ball_pos[1])

        # 显示在击球位置右上方
        text_x = bx + 15
        text_y = by - 15

        speed_text = f"P{overlay['player_id']} {overlay['speed_ms']} m/s"
        color_intensity = int(255 * alpha)
        color = (0, color_intensity, color_intensity)  # 青色渐淡

        overlay_img = frame.copy()
        # 文字背景
        (tw, th), _ = cv2.getTextSize(speed_text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.rectangle(
            overlay_img,
            (text_x - 4, text_y - th - 4),
            (text_x + tw + 4, text_y + 4),
            (0, 0, 0),
            -1,
        )
        cv2.addWeighted(overlay_img, 0.45, frame, 0.55, 0, frame)

        cv2.putText(
            frame, speed_text, (text_x, text_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2,
        )

    return frame


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


def _detect_landing_points(
    ball_detections: list[dict[int, list[float]]],
    video_fps: float,
    court_keypoints: list[float] | None = None,
) -> list[dict]:
    """
    检测网球落地点（弹跳/撞地时刻）

    算法：
    1. 对每个 track_id 提取 Y 坐标轨迹
    2. 平滑后寻找局部 Y 极大值（图像坐标系中球最低点 ≈ 落地）
    3. 过滤：只在球场范围内、且连续落地间隔 > 15 帧

    Args:
        ball_detections: 逐帧球检测 [{track_id: [x1,y1,x2,y2]}, ...]
        video_fps: 视频帧率
        court_keypoints: 28 个场地关键点（用于过滤球场外落地）

    Returns:
        [{"frame_idx": int, "time_s": float, "ball_pos": (x, y),
          "ball_center": (x, y), "track_id": int}, ...]
    """
    if not ball_detections:
        return []

    all_track_ids: set[int] = set()
    for det in ball_detections:
        all_track_ids.update(det.keys())
    if not all_track_ids:
        return []

    landing_points = []

    for track_id in all_track_ids:
        positions: list[list[float]] = [
            det.get(track_id, []) for det in ball_detections
        ]
        df = pd.DataFrame(positions, columns=["x1", "y1", "x2", "y2"])

        # 提取中心点 Y 坐标
        cy = (df["y1"] + df["y2"]) / 2
        valid = cy.notna()
        if valid.sum() < 10:
            continue

        # 平滑
        y_vals = cy[valid].values.astype(float)
        if len(y_vals) >= 7:
            y_vals = savgol_filter(y_vals, window_length=7, polyorder=2)
        elif len(y_vals) >= 5:
            y_vals = savgol_filter(y_vals, window_length=5, polyorder=2)

        # 找 Y 极大值（图像中最低点 = 落地）
        peaks, props = find_peaks(y_vals, distance=15, prominence=5)

        if len(peaks) == 0:
            continue

        # 映射回原始帧索引
        valid_indices = np.where(valid)[0]
        bounce_frames = [valid_indices[p] for p in peaks]

        # 球场范围过滤
        net_y = None
        if court_keypoints and len(court_keypoints) >= 28:
            net_y = (court_keypoints[25] + court_keypoints[27]) / 2.0

        for bf_idx in bounce_frames:
            if bf_idx >= len(ball_detections):
                continue
            ball_dict = ball_detections[bf_idx]
            bbox = ball_dict.get(track_id)
            if not bbox or len(bbox) < 4:
                continue

            cx = (bbox[0] + bbox[2]) / 2
            cy_val = (bbox[1] + bbox[3]) / 2

            # 过滤球场外落地（如果知道网线位置）
            if net_y is not None:
                if cy_val < net_y - 50:
                    continue

            landing_points.append({
                "frame_idx": bf_idx,
                "time_s": bf_idx / video_fps,
                "ball_pos": (int(cx), int(cy_val)),
                "ball_center": (cx, cy_val),
                "track_id": track_id,
            })

    return landing_points


def _init_landing_markers(landing_points: list[dict], video_fps: float):
    """初始化落地点标记的视觉效果"""
    global _landing_markers
    _landing_markers = []

    for lp in landing_points:
        pos = lp.get("ball_pos", (0, 0))
        frame_idx = lp["frame_idx"]

        _landing_markers.append({
            "frame_start": frame_idx,
            "duration_frames": max(int(0.5 * video_fps), 1),
            "x": pos[0],
            "y": pos[1],
        })

    logger.info(f"Initialized {len(_landing_markers)} landing markers")


def _draw_landing_markers(frame: np.ndarray, current_frame_idx: int) -> np.ndarray:
    """在当前帧绘制落地点标记（地面冲击波 + 十字）"""
    global _landing_markers

    if not _landing_markers:
        return frame

    h, w = frame.shape[:2]

    for marker in _landing_markers:
        age = current_frame_idx - marker["frame_start"]
        if age < 0 or age > marker["duration_frames"]:
            continue

        progress = age / marker["duration_frames"]
        alpha = max(0, 1.0 - progress ** 1.5)
        if alpha <= 0:
            continue

        cx, cy = int(marker["x"]), int(marker["y"])
        if not (0 <= cx < w and 0 <= cy < h):
            continue

        # 冲击波圆环（从小到大扩散）
        for i, (radius, r_alpha) in enumerate([
            (12, alpha * 0.3),
            (8, alpha * 0.5),
            (5, alpha * 0.7),
        ]):
            overlay = frame.copy()
            cv2.circle(overlay, (cx, cy), radius, (0, 200, 255), -1)
            cv2.addWeighted(overlay, r_alpha, frame, 1 - r_alpha, 0, frame)

        # 十字标记
        cross_size = 8
        color = (0, 255, 255)  # 黄色
        int_alpha = int(255 * alpha)
        cv2.line(frame, (cx - cross_size, cy), (cx + cross_size, cy),
                 (color[0], color[1], color[2]), 2)
        cv2.line(frame, (cx, cy - cross_size), (cx, cy + cross_size),
                 (color[0], color[1], color[2]), 2)

        # 外侧描边（白色高亮）
        if alpha > 0.4:
            cv2.line(frame, (cx - cross_size - 1, cy), (cx + cross_size + 1, cy),
                     (255, 255, 255), 1)
            cv2.line(frame, (cx, cy - cross_size - 1), (cx, cy + cross_size + 1),
                     (255, 255, 255), 1)

        # 底部阴影椭圆（模拟地面投影）
        overlay = frame.copy()
        cv2.ellipse(overlay, (cx, cy + 3), (22, 6), 0, 0, 360,
                    (0, 0, 0), -1)
        cv2.addWeighted(overlay, alpha * 0.3, frame, 1 - alpha * 0.3, 0, frame)

    return frame


def _draw_mini_court_overlay(
    frame: np.ndarray,
    current_frame_idx: int,
    court_coordinates: dict,
    ball_detections: list[dict[int, list[float]]],
    ball_mini_coords: list[dict[int, tuple]],
) -> np.ndarray:
    """
    在帧右下角绘制迷你球场轨迹可视化

    绘制内容：
    - 半透明深色背景卡片
    - 迷你球场线条（基于 mini_court_keypoints）
    - 球轨迹点（映射到小场地）
    - 当前球位置亮点
    """
    h, w = frame.shape[:2]

    mini_court_kps = court_coordinates.get("mini_court_keypoints", [])
    if not mini_court_kps or not ball_mini_coords:
        return frame

    # 卡片位置：右下角
    card_w = 280
    card_h = 340
    card_x1 = w - card_w - 15
    card_y1 = h - card_h - 15
    card_x2 = w - 15
    card_y2 = h - 15

    # 半透明背景卡片
    overlay = frame.copy()
    cv2.rectangle(overlay, (card_x1, card_y1), (card_x2, card_y2), (28, 28, 28), -1)
    cv2.rectangle(overlay, (card_x1, card_y1), (card_x2, card_y2), (80, 80, 80), 1)
    cv2.addWeighted(overlay, 0.62, frame, 0.38, 0, frame)

    # 标题
    cv2.putText(frame, "COURT VIEW", (card_x1 + 10, card_y1 + 22),
                cv2.FONT_HERSHEY_DUPLEX, 0.5, (180, 180, 180), 1, cv2.LINE_AA)

    # 计算迷你球场在卡片内的绘制区域
    padding = 20
    court_draw_x1 = card_x1 + padding
    court_draw_y1 = card_y1 + 35
    court_draw_x2 = card_x2 - padding
    court_draw_y2 = card_y2 - padding
    court_draw_w = court_draw_x2 - court_draw_x1
    court_draw_h = court_draw_y2 - court_draw_y1

    # 绘制球场网格线（使用 mini_court_keypoints 定义的关系）
    # keypoints: 28 值 = 14 个点 (x0,y0)..(x13,y13)
    if len(mini_court_kps) >= 28:
        # 提取所有关键点
        pts = [(mini_court_kps[i], mini_court_kps[i + 1]) for i in range(0, 28, 2)]

        # 定义球场线连接关系
        court_lines = [
            (0, 2), (4, 5), (6, 7), (1, 3),   # 底线
            (0, 1), (8, 9), (10, 11), (2, 3),  # 边线
            (4, 6), (12, 13),                  # 中线/网
        ]

        # 获取所有参考点的边界
        all_x = [p[0] for p in pts[:14]]
        all_y = [p[1] for p in pts[:14]]
        ref_min_x, ref_max_x = min(all_x), max(all_x)
        ref_min_y, ref_max_y = min(all_y), max(all_y)
        ref_w = max(ref_max_x - ref_min_x, 1)
        ref_h = max(ref_max_y - ref_min_y, 1)

        # 绘制场地线
        for line_pair in court_lines:
            p1 = pts[line_pair[0]]
            p2 = pts[line_pair[1]]
            if p1 and p2:
                x1_n = int(court_draw_x1 + (p1[0] - ref_min_x) / ref_w * court_draw_w)
                y1_n = int(court_draw_y1 + (p1[1] - ref_min_y) / ref_h * court_draw_h)
                x2_n = int(court_draw_x1 + (p2[0] - ref_min_x) / ref_w * court_draw_w)
                y2_n = int(court_draw_y1 + (p2[1] - ref_min_y) / ref_h * court_draw_h)
                cv2.line(frame, (x1_n, y1_n), (x2_n, y2_n), (140, 140, 140), 1)

        # 标记网线（更粗醒目）
        if len(pts) > 13:
            p12 = pts[12]
            p13 = pts[13]
            x12 = int(court_draw_x1 + (p12[0] - ref_min_x) / ref_w * court_draw_w)
            y12 = int(court_draw_y1 + (p12[1] - ref_min_y) / ref_h * court_draw_h)
            x13 = int(court_draw_x1 + (p13[0] - ref_min_x) / ref_w * court_draw_w)
            y13 = int(court_draw_y1 + (p13[1] - ref_min_y) / ref_h * court_draw_h)
            cv2.line(frame, (x12, y12), (x13, y13), (255, 200, 50), 2)
            cv2.putText(frame, "NET", (x12, y12 - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 200, 50), 1)

        # 绘制球轨迹点
        track_colors = [
            (0, 255, 255), (255, 0, 0), (0, 255, 0),
            (255, 0, 255), (255, 128, 0), (0, 128, 255),
        ]

        active_tracks: dict[int, list[tuple]] = {}
        # 收集最近 60 帧的每个 track 的 mini-court 坐标
        lookback = min(60, current_frame_idx)
        for f_idx in range(max(0, current_frame_idx - lookback), current_frame_idx + 1):
            if f_idx < len(ball_mini_coords):
                for tid, pos in ball_mini_coords[f_idx].items():
                    if pos and len(pos) >= 2 and pos != (0, 0):
                        if tid not in active_tracks:
                            active_tracks[tid] = []
                        active_tracks[tid].append(pos)

        for tid, coords in active_tracks.items():
            color = track_colors[tid % len(track_colors)]

            # 绘制轨迹线
            for i in range(1, len(coords)):
                p1 = coords[i - 1]
                p2 = coords[i]
                x1_m = int(court_draw_x1 + (p1[0] - ref_min_x) / ref_w * court_draw_w)
                y1_m = int(court_draw_y1 + (p1[1] - ref_min_y) / ref_h * court_draw_h)
                x2_m = int(court_draw_x1 + (p2[0] - ref_min_x) / ref_w * court_draw_w)
                y2_m = int(court_draw_y1 + (p2[1] - ref_min_y) / ref_h * court_draw_h)
                # 渐淡效果
                t = i / len(coords)
                alpha_t = 0.3 + t * 0.7
                overlay = frame.copy()
                cv2.line(overlay, (x1_m, y1_m), (x2_m, y2_m), color, 2)
                cv2.addWeighted(overlay, alpha_t * 0.5, frame, 1 - alpha_t * 0.5, 0, frame)

            # 当前球位置亮点
            if coords:
                last = coords[-1]
                lx = int(court_draw_x1 + (last[0] - ref_min_x) / ref_w * court_draw_w)
                ly = int(court_draw_y1 + (last[1] - ref_min_y) / ref_h * court_draw_h)
                cv2.circle(frame, (lx, ly), 5, color, -1)
                cv2.circle(frame, (lx, ly), 3, (255, 255, 255), -1)

    return frame


def draw_ball_trajectory_video(
    video_path: Path,
    ball_detections: list[dict[int, list[float]]],
    output_path: Path,
    range_start_s: float = 0.0,
    sample_fps: float = 4.0,
    progress_cb: ProgressCallback | None = None,
    hit_moments: list[dict] | None = None,
    player_detections: list[dict[int, list[float]]] | None = None,
    speed_events: dict | None = None,
    court_coordinates: dict | None = None,
    ball_mini_coords: list[dict[int, tuple]] | None = None,
    player_mini_coords: list[dict[int, tuple]] | None = None,
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
        player_detections: 逐帧球员检测框（用于球速文字定位）
        speed_events: 速度事件数据（含 ball_speeds, player_speeds）
        court_coordinates: 球场坐标信息（含 mini_court_keypoints 用于迷你球场渲染）
        ball_mini_coords: 逐帧球在小场地上的坐标 [{track_id: (x, y)}, ...]
        player_mini_coords: 逐帧球员在小场地上的坐标（暂未使用，保留扩展）

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

    # 使用 ffmpeg pipe 直接编码 H.264，浏览器兼容
    ffmpeg_cmd = [
        'ffmpeg',
        '-y',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-s', f'{width}x{height}',
        '-pix_fmt', 'bgr24',
        '-r', str(video_fps),
        '-i', '-',
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-preset', 'fast',
        '-crf', '23',
        str(output_path),
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)

    logger.info(f"Rendering ball trajectory video: {width}x{height} @ {video_fps}fps, {total_frames} frames")

    # 跳到起始位置
    start_frame = int(range_start_s * video_fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    # 用于绘制轨迹 - 按 track_id 分开存储
    trajectory_points: dict[int, list[tuple[int, int]]] = {}
    # 为每个 track_id 分配不同颜色 (BGR 格式)
    trajectory_colors = [
        (0, 255, 255),    # 黄色 - 默认球的颜色
        (255, 0, 0),      # 蓝色
        (0, 255, 0),      # 绿色
        (255, 0, 255),    # 紫色
        (255, 128, 0),    # 青色
        (0, 128, 255),    # 橙色
        (128, 255, 255),  # 浅黄
        (255, 255, 0),    # 红色
    ]

    # 初始化火花粒子系统
    if hit_moments:
        _init_sparks(hit_moments, video_fps)
        logger.info(f"Initialized spark effects for {len(hit_moments)} hit moments")

    # 初始化速度叠加系统
    if speed_events:
        _init_speed_overlays(speed_events, video_fps)

    # 初始化落地点标记
    landing_points = _detect_landing_points(
        ball_detections, video_fps,
        court_keypoints=court_coordinates.get("original_keypoints") if court_coordinates else None,
    )
    if landing_points:
        _init_landing_markers(landing_points, video_fps)

    try:
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # 绘制火花效果（在球之前）
            if hit_moments:
                frame = _draw_sparks(frame, frame_idx)

            # 绘制速度叠加（球速文本 + 跑动连线）
            if speed_events:
                frame = _draw_speed_overlays(frame, frame_idx)

            # 绘制落地点标记
            if landing_points:
                frame = _draw_landing_markers(frame, frame_idx)

            # 逐帧绘制检测结果
            if frame_idx < len(ball_detections):
                ball_dict = ball_detections[frame_idx]

                for track_id, bbox in ball_dict.items():
                    x1, y1, x2, y2 = bbox

                    # 计算中心点
                    cx = int((x1 + x2) / 2)
                    cy = int((y1 + y2) / 2)

                    # 为该球的轨迹初始化
                    if track_id not in trajectory_points:
                        trajectory_points[track_id] = []
                    trajectory_points[track_id].append((cx, cy))

                    # 获取该球的颜色
                    ball_color = trajectory_colors[track_id % len(trajectory_colors)]

                    # 绘制该球的轨迹曲线 (Catmull-Rom 平滑)
                    pts = trajectory_points[track_id]
                    if len(pts) >= 2:
                        # 取最近 7 个点作为控制点
                        trail_length = min(7, len(pts))
                        start_idx = len(pts) - trail_length
                        raw_pts = pts[start_idx:]

                        # 通过 Catmull-Rom 样条生成平滑插值点
                        smooth_pts = []
                        for i in range(len(raw_pts) - 1):
                            p0 = raw_pts[max(0, i - 1)]
                            p1 = raw_pts[i]
                            p2 = raw_pts[i + 1]
                            p3 = raw_pts[min(len(raw_pts) - 1, i + 2)]
                            chunk = _catmull_rom_chunk(p0, p1, p2, p3, num_pts=8)
                            smooth_pts.extend(chunk)

                        # 三层发光绘制
                        for layer in range(3):
                            layer_alpha = 0.25 + layer * 0.2
                            for i in range(1, len(smooth_pts)):
                                t = i / (len(smooth_pts) - 1)
                                # 球的主体颜色保持不变，亮度渐变
                                b = int(ball_color[0] * (t * 0.3 + 0.7))
                                g = int(ball_color[1] * (t * 0.3 + 0.7))
                                r_val = int(ball_color[2] * (t * 0.3 + 0.7))
                                color = (b, g, r_val)
                                thickness = 3 + layer * 2

                                overlay = frame.copy()
                                cv2.line(overlay, smooth_pts[i - 1], smooth_pts[i], color, thickness)
                                cv2.addWeighted(overlay, layer_alpha, frame, 1 - layer_alpha, 0, frame)

                    # 绘制球标记框（使用对应颜色）
                    cv2.putText(frame, f"Ball {track_id}", (int(bbox[0]), int(bbox[1] - 10)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, ball_color, 2)
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), ball_color, 2)

            # 绘制迷你球场轨迹可视化（最上层）
            if court_coordinates and ball_mini_coords:
                frame = _draw_mini_court_overlay(
                    frame, frame_idx, court_coordinates,
                    ball_detections, ball_mini_coords,
                )

            # 写入帧到 ffmpeg stdin
            proc.stdin.write(frame.tobytes())
            frame_idx += 1

            # 报告进度
            if progress_cb and frame_idx % 30 == 0:
                pct = (frame_idx / total_frames) * 100 if total_frames > 0 else 0
                progress_cb(min(95.0, pct + 5.0), f"Rendering frame {frame_idx}/{total_frames}", None)

    finally:
        cap.release()
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed with return code {proc.returncode}")
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
    player_detections = artifact.get("player_detections", None)
    speed_events = artifact.get("speed_events", None)
    court_coordinates = artifact.get("court_coordinates", None)
    ball_mini_coords = artifact.get("ball_mini_coords", None)

    return draw_ball_trajectory_video(
        video_path=video_path,
        ball_detections=ball_detections,
        output_path=output_path,
        range_start_s=metadata.get("range_start_s", 0.0),
        sample_fps=metadata.get("sample_fps", 4.0),
        progress_cb=progress_cb,
        hit_moments=hit_moments if hit_moments else None,
        player_detections=player_detections,
        speed_events=speed_events,
        court_coordinates=court_coordinates,
        ball_mini_coords=ball_mini_coords,
    )
