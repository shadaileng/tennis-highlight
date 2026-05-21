"""
Render Pipeline - 组合式渲染管道

每个功能作为一个独立的渲染层（Render Pass），
由 RenderPipeline 统一编排和开关控制。
"""

import logging
import math
import random
from abc import ABC, abstractmethod

import cv2
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter

logger = logging.getLogger(__name__)


# ============================================================
# 工具函数
# ============================================================

def _catmull_rom_chunk(p0, p1, p2, p3, num_pts: int = 8) -> list[tuple[int, int]]:
    """Catmull-Rom 样条插值 p1→p2，生成 num_pts 个平滑插值点"""
    pts = []
    for i in range(num_pts):
        t = i / (num_pts - 1) if num_pts > 1 else 0
        t2 = t * t
        t3 = t2 * t
        x = int(0.5 * (
            2 * p1[0] + (-p0[0] + p2[0]) * t +
            (2 * p1[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
            (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
        ))
        y = int(0.5 * (
            2 * p1[1] + (-p0[1] + p2[1]) * t +
            (2 * p1[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
            (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
        ))
        pts.append((x, y))
    return pts


def _get_diamond_vertices(
    cx: int, cy: int,
    direction_angle: float,
    distance: float,
    length: float,
    width_ratio: float,
) -> list[tuple[int, int]]:
    """计算细长菱形的4个顶点"""
    if length < 3:
        return []
    cos_dir = np.cos(direction_angle)
    sin_dir = np.sin(direction_angle)
    perp_cos = np.cos(direction_angle + np.pi / 2)
    perp_sin = np.sin(direction_angle + np.pi / 2)
    center_x = cx + cos_dir * distance
    center_y = cy + sin_dir * distance
    half_width = length * width_ratio * 0.5
    far_extent = length * 0.65
    near_offset = length * 0.2
    near_extent = length * 0.35
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
    intensity = int(255 * min(1.0, alpha * 1.5))
    color = (intensity, intensity, intensity)
    # 外层光晕
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
    # 主菱形
    overlay = frame.copy()
    cv2.fillPoly(overlay, [pts], color)
    cv2.addWeighted(overlay, alpha * 0.4, frame, 1 - alpha * 0.4, 0, frame)
    # 核心亮边
    if alpha > 0.15:
        overlay = frame.copy()
        cv2.polylines(overlay, [pts], True, (255, 255, 255), 1)
        cv2.addWeighted(overlay, alpha * 0.7, frame, 1 - alpha * 0.7, 0, frame)


def _detect_landing_points(
    ball_detections: list[dict[int, list[float]]],
    video_fps: float,
    court_keypoints: list[float] | None = None,
) -> list[dict]:
    """检测网球落地点（弹跳/撞地时刻）"""
    if not ball_detections:
        return []
    all_track_ids: set[int] = set()
    for det in ball_detections:
        all_track_ids.update(det.keys())
    if not all_track_ids:
        return []
    landing_points = []
    for track_id in all_track_ids:
        positions: list[list[float]] = [det.get(track_id, []) for det in ball_detections]
        df = pd.DataFrame(positions, columns=["x1", "y1", "x2", "y2"])
        cy = (df["y1"] + df["y2"]) / 2
        valid = cy.notna()
        if valid.sum() < 10:
            continue
        y_vals = cy[valid].values.astype(float)
        if len(y_vals) >= 7:
            y_vals = savgol_filter(y_vals, window_length=7, polyorder=2)
        elif len(y_vals) >= 5:
            y_vals = savgol_filter(y_vals, window_length=5, polyorder=2)
        peaks, props = find_peaks(y_vals, distance=15, prominence=5)
        if len(peaks) == 0:
            continue
        valid_indices = np.where(valid)[0]
        bounce_frames = [valid_indices[p] for p in peaks]
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
            if net_y is not None and cy_val < net_y - 50:
                continue
            landing_points.append({
                "frame_idx": bf_idx,
                "time_s": bf_idx / video_fps,
                "ball_pos": (int(cx), int(cy_val)),
                "ball_center": (cx, cy_val),
                "track_id": track_id,
            })
    return landing_points


# ============================================================
# RenderPass 基类
# ============================================================

class RenderPass(ABC):
    """渲染层基类"""
    def __init__(self):
        self.enabled = True

    def initialize(self):
        """初始化渲染层状态（在渲染开始前调用一次）"""
        pass

    @abstractmethod
    def draw(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        """在帧上绘制当前渲染层的内容"""
        pass


# ============================================================
# RenderPipeline 编排器
# ============================================================

class RenderPipeline:
    """渲染管道 - 组合多个渲染层并逐帧执行"""

    def __init__(self, width: int, height: int, video_fps: float):
        self.width = width
        self.height = height
        self.video_fps = video_fps
        self._passes: list[RenderPass] = []
        self._pass_map: dict[str, RenderPass] = {}

    def add_pass(self, rp: RenderPass):
        """注册渲染层"""
        self._passes.append(rp)
        self._pass_map[type(rp).__name__] = rp

    def get_pass(self, name: str) -> RenderPass | None:
        return self._pass_map.get(name)

    def set_enabled(self, name: str, enabled: bool):
        if name in self._pass_map:
            self._pass_map[name].enabled = enabled

    def initialize_all(self):
        """初始化所有已启用的渲染层"""
        for rp in self._passes:
            if rp.enabled:
                rp.initialize()

    def render_frame(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        """逐帧执行所有启用的渲染层"""
        for rp in self._passes:
            if rp.enabled:
                frame = rp.draw(frame, frame_idx)
        return frame


# ============================================================
# SparkPass - 击球火花效果
# ============================================================

class SparkPass(RenderPass):
    """击球火花效果渲染层"""
    def __init__(self, hit_moments: list[dict], video_fps: float):
        super().__init__()
        self.hit_moments = hit_moments or []
        self.video_fps = video_fps
        self.effects: list[dict] = []

    def initialize(self):
        self.effects = []
        if not self.hit_moments:
            return
        for hit in self.hit_moments:
            frame_idx = hit.get("frame_idx", int(hit.get("time_s", 0) * self.video_fps))
            pos = hit.get("ball_pos", (0, 0))
            if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                continue
            num_diamonds = random.randint(15, 25)
            diamonds = []
            for _ in range(num_diamonds):
                angle = random.uniform(0, 2 * np.pi)
                speed = random.uniform(10, 25)
                length = random.uniform(35, 70)
                width_ratio = random.uniform(0.08, 0.2)
                diamonds.append({"angle": angle, "speed": speed, "length": length, "width_ratio": width_ratio})
            self.effects.append({
                "frame_start": frame_idx,
                "duration_frames": int(0.5 * self.video_fps),
                "x": pos[0], "y": pos[1],
                "diamonds": diamonds,
            })
        logger.info(f"SparkPass: initialized {len(self.effects)} effects")

    def draw(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        if not self.effects:
            return frame
        h, w = frame.shape[:2]
        for effect in self.effects:
            age = frame_idx - effect["frame_start"]
            if age < 0 or age > effect["duration_frames"]:
                continue
            progress = age / effect["duration_frames"]
            alpha = max(0, 1.0 - progress ** 1.2)
            if alpha <= 0:
                continue
            cx, cy = int(effect["x"]), int(effect["y"])
            for diamond in effect["diamonds"]:
                distance = diamond["speed"] * age
                size_scale = 1.2 - progress * 1.2
                if size_scale <= 0:
                    continue
                _draw_diamond(frame, cx, cy, diamond["angle"], distance,
                              diamond["length"] * size_scale, diamond["width_ratio"], alpha)
            if alpha > 0.2:
                for radius, r_alpha in [(10, 0.25 * alpha), (5, 0.4 * alpha)]:
                    overlay = frame.copy()
                    cv2.circle(overlay, (cx, cy), radius, (255, 255, 255), -1)
                    cv2.addWeighted(overlay, r_alpha, frame, 1 - r_alpha, 0, frame)
        return frame


# ============================================================
# BallSpeedPass - 球速文本
# ============================================================

class BallSpeedPass(RenderPass):
    """球速文本渲染层"""
    def __init__(self, speed_events: dict | None, video_fps: float):
        super().__init__()
        self.speed_events = speed_events
        self.video_fps = video_fps
        self.overlays: list[dict] = []

    def initialize(self):
        self.overlays = []
        if not self.speed_events:
            return
        near_player_id = self.speed_events.get("near_player_id")
        for bs in self.speed_events.get("ball_speeds", []):
            if near_player_id is not None and bs.get("hitter_id") != near_player_id:
                continue
            duration_frames = max(int(0.8 * self.video_fps), 1)
            self.overlays.append({
                "frame_start": bs["frame_idx"],
                "duration_frames": duration_frames,
                "speed_ms": bs["speed_ms"],
                "ball_pos": bs.get("ball_pos"),
                "player_id": bs.get("hitter_id"),
            })

    def draw(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        if not self.overlays:
            return frame
        for overlay in self.overlays:
            age = frame_idx - overlay["frame_start"]
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
            text_x, text_y = bx + 15, by - 15
            speed_text = f"P{overlay['player_id']} {overlay['speed_ms']} m/s"
            color_intensity = int(255 * alpha)
            color = (0, color_intensity, color_intensity)
            overlay_img = frame.copy()
            (tw, th), _ = cv2.getTextSize(speed_text, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            cv2.rectangle(overlay_img, (text_x - 4, text_y - th - 4),
                          (text_x + tw + 4, text_y + 4), (0, 0, 0), -1)
            cv2.addWeighted(overlay_img, 0.45, frame, 0.55, 0, frame)
            cv2.putText(frame, speed_text, (text_x, text_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        return frame


# ============================================================
# RunningSpeedPass - 跑动速度连线
# ============================================================

class RunningSpeedPass(RenderPass):
    """跑动速度连线渲染层"""
    def __init__(self, speed_events: dict | None, video_fps: float):
        super().__init__()
        self.speed_events = speed_events
        self.video_fps = video_fps
        self.overlays: list[dict] = []

    def initialize(self):
        self.overlays = []
        if not self.speed_events:
            return
        near_player_id = self.speed_events.get("near_player_id")
        for ps in self.speed_events.get("player_speeds", []):
            if near_player_id is not None and ps.get("player_id") != near_player_id:
                continue
            duration_frames = max(int(1.0 * self.video_fps), 1)
            self.overlays.append({
                "frame_start": max(0, ps["end_frame"] - duration_frames),
                "duration_frames": duration_frames,
                "speed_ms": ps["speed_ms"],
                "start_pos": ps.get("start_pos"),
                "end_pos": ps.get("end_pos"),
                "player_id": ps.get("player_id"),
            })

    def draw(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        if not self.overlays:
            return frame
        h, w = frame.shape[:2]
        for overlay in self.overlays:
            age = frame_idx - overlay["frame_start"]
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
            if not (0 <= p1[0] < w and 0 <= p1[1] < h and 0 <= p2[0] < w and 0 <= p2[1] < h):
                continue
            extend_phase = 0.6
            extend_progress = min(1.0, progress / extend_phase) if extend_phase > 0 else 1.0
            alpha = max(0, 1.0 - (progress - extend_phase) / (1.0 - extend_phase)) if progress > extend_phase else 1.0
            if extend_progress < 1.0:
                current_end = (
                    int(p1[0] + (p2[0] - p1[0]) * extend_progress),
                    int(p1[1] + (p2[1] - p1[1]) * extend_progress),
                )
                mid_x = (p1[0] + current_end[0]) // 2
                mid_y = (p1[1] + current_end[1]) // 2
            else:
                current_end = p2
                mid_x = (p1[0] + p2[0]) // 2
                mid_y = (p1[1] + p2[1]) // 2
            if alpha <= 0:
                continue
            overlay_img = frame.copy()
            cv2.line(overlay_img, p1, current_end, (0, 255, 255), 3)
            cv2.addWeighted(overlay_img, alpha * 0.6, frame, 1 - alpha * 0.6, 0, frame)
            if extend_progress < 1.0:
                cv2.circle(frame, current_end, 5, (0, 255, 255), -1)
                cv2.circle(frame, current_end, 8, (255, 255, 255), 2)
            speed_text = f"P{overlay['player_id']} {overlay['speed_ms']} m/s"
            (tw, th), _ = cv2.getTextSize(speed_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            text_x = mid_x - tw // 2
            text_y = mid_y - 10
            overlay_img2 = frame.copy()
            cv2.rectangle(overlay_img2, (text_x - 4, text_y - th - 4),
                          (text_x + tw + 4, text_y + 4), (0, 0, 0), -1)
            cv2.addWeighted(overlay_img2, 0.5, frame, 0.5, 0, frame)
            cv2.putText(frame, speed_text, (text_x, text_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        return frame


# ============================================================
# BallTrajectoryPass - 网球轨迹
# ============================================================

class BallTrajectoryPass(RenderPass):
    """网球轨迹渲染层"""
    def __init__(self, ball_detections: list[dict[int, list[float]]], width: int, height: int):
        super().__init__()
        self.ball_detections = ball_detections
        self.width = width
        self.height = height
        self.trajectory_points: dict[int, list[tuple[int, int]]] = {}
        self.trajectory_colors = [
            (0, 255, 255), (255, 0, 0), (0, 255, 0),
            (255, 0, 255), (255, 128, 0), (0, 128, 255),
            (128, 255, 255), (255, 255, 0),
        ]

    def initialize(self):
        self.trajectory_points = {}

    def draw(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        if frame_idx >= len(self.ball_detections):
            return frame
        ball_dict = self.ball_detections[frame_idx]
        for track_id, bbox in ball_dict.items():
            x1, y1, x2, y2 = bbox
            cx = int((x1 + x2) / 2)
            cy = int((y1 + y2) / 2)
            if track_id not in self.trajectory_points:
                self.trajectory_points[track_id] = []
            self.trajectory_points[track_id].append((cx, cy))
            ball_color = self.trajectory_colors[int(track_id) % len(self.trajectory_colors)]
            pts = self.trajectory_points[track_id]
            if len(pts) >= 2:
                trail_length = min(7, len(pts))
                start_idx = len(pts) - trail_length
                raw_pts = pts[start_idx:]
                smooth_pts = []
                for i in range(len(raw_pts) - 1):
                    p0 = raw_pts[max(0, i - 1)]
                    p1 = raw_pts[i]
                    p2 = raw_pts[i + 1]
                    p3 = raw_pts[min(len(raw_pts) - 1, i + 2)]
                    chunk = _catmull_rom_chunk(p0, p1, p2, p3, num_pts=8)
                    smooth_pts.extend(chunk)
                for layer in range(3):
                    layer_alpha = 0.25 + layer * 0.2
                    for i in range(1, len(smooth_pts)):
                        t = i / (len(smooth_pts) - 1)
                        b = int(ball_color[0] * (t * 0.3 + 0.7))
                        g = int(ball_color[1] * (t * 0.3 + 0.7))
                        r_val = int(ball_color[2] * (t * 0.3 + 0.7))
                        color = (b, g, r_val)
                        thickness = 3 + layer * 2
                        overlay = frame.copy()
                        cv2.line(overlay, smooth_pts[i - 1], smooth_pts[i], color, thickness)
                        cv2.addWeighted(overlay, layer_alpha, frame, 1 - layer_alpha, 0, frame)
            cv2.putText(frame, f"Ball {track_id}", (int(bbox[0]), int(bbox[1] - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, ball_color, 2)
            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), ball_color, 2)
        return frame


# ============================================================
# LandingMarkerPass - 落地点标记
# ============================================================

class LandingMarkerPass(RenderPass):
    """落地点标记渲染层"""
    def __init__(self, ball_detections: list[dict[int, list[float]]], video_fps: float,
                 court_keypoints: list[float] | None = None):
        super().__init__()
        self.ball_detections = ball_detections
        self.video_fps = video_fps
        self.court_keypoints = court_keypoints
        self.landing_points: list[dict] = []
        self.markers: list[dict] = []

    def initialize(self):
        self.landing_points = _detect_landing_points(
            self.ball_detections, self.video_fps,
            court_keypoints=self.court_keypoints,
        )
        self.markers = []
        for lp in self.landing_points:
            pos = lp.get("ball_pos", (0, 0))
            self.markers.append({
                "frame_start": lp["frame_idx"],
                "duration_frames": max(int(0.5 * self.video_fps), 1),
                "x": pos[0], "y": pos[1],
            })
        if self.markers:
            logger.info(f"LandingMarkerPass: initialized {len(self.markers)} markers")

    def draw(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        if not self.markers:
            return frame
        h, w = frame.shape[:2]
        for marker in self.markers:
            age = frame_idx - marker["frame_start"]
            if age < 0 or age > marker["duration_frames"]:
                continue
            progress = age / marker["duration_frames"]
            alpha = max(0, 1.0 - progress ** 1.5)
            if alpha <= 0:
                continue
            cx, cy = int(marker["x"]), int(marker["y"])
            if not (0 <= cx < w and 0 <= cy < h):
                continue
            for radius, r_alpha in [(12, alpha * 0.3), (8, alpha * 0.5), (5, alpha * 0.7)]:
                overlay = frame.copy()
                cv2.circle(overlay, (cx, cy), radius, (0, 200, 255), -1)
                cv2.addWeighted(overlay, r_alpha, frame, 1 - r_alpha, 0, frame)
            cross_size = 8
            cv2.line(frame, (cx - cross_size, cy), (cx + cross_size, cy), (0, 255, 255), 2)
            cv2.line(frame, (cx, cy - cross_size), (cx, cy + cross_size), (0, 255, 255), 2)
            if alpha > 0.4:
                cv2.line(frame, (cx - cross_size - 1, cy), (cx + cross_size + 1, cy), (255, 255, 255), 1)
                cv2.line(frame, (cx, cy - cross_size - 1), (cx, cy + cross_size + 1), (255, 255, 255), 1)
            overlay = frame.copy()
            cv2.ellipse(overlay, (cx, cy + 3), (22, 6), 0, 0, 360, (0, 0, 0), -1)
            cv2.addWeighted(overlay, alpha * 0.3, frame, 1 - alpha * 0.3, 0, frame)
        return frame


# ============================================================
# CourtAnimationPass - 开场一笔画成球场
# ============================================================

class CourtAnimationPass(RenderPass):
    """开场网球场关键点连线高亮频闪动画"""

    # 所有球场线段（14 关键点索引对）
    COURT_EDGES = [
        (0, 2), (2, 3), (3, 1), (1, 0),      # 外围 doubles 矩形
        (0, 4), (4, 5), (5, 7), (7, 6),      # singles 线
        (6, 4),                               # 上单打线
        (4, 8), (8, 9), (9, 11), (11, 10),   # 发球区
        (10, 12), (12, 13),                   # 球网
    ]

    def __init__(self, court_coordinates: dict | None, video_fps: float):
        super().__init__()
        self.court_coordinates = court_coordinates
        self.video_fps = video_fps
        self._segments: list[dict] = []
        self._keypoints: list[tuple[int, int]] = []

    def initialize(self):
        self._segments = []
        self._keypoints = []
        if not self.court_coordinates:
            self.enabled = False
            return
        kps = self.court_coordinates.get("original_keypoints", [])
        if not kps or len(kps) < 28:
            self.enabled = False
            return
        pts = [(int(kps[i]), int(kps[i + 1])) for i in range(0, 28, 2)]
        for i1, i2 in self.COURT_EDGES:
            if i1 < len(pts) and i2 < len(pts):
                self._segments.append({"p1": pts[i1], "p2": pts[i2]})
        for pt in pts:
            self._keypoints.append(pt)
        if not self._segments:
            self.enabled = False

    def draw(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        appear_frames = max(int(0.15 * self.video_fps), 1)        # 快速出现
        strobe_frames = max(int(1.5 * self.video_fps), 1)         # 频闪
        fade_frames = max(int(0.35 * self.video_fps), 1)          # 淡出
        total_frames = appear_frames + strobe_frames + fade_frames

        if frame_idx >= total_frames:
            # 动画结束后绘制持续低透明度球场叠加层
            self._draw_persistent_court(frame, alpha=0.15)
            return frame

        alpha = 0.0

        # 阶段 1: 快速淡入
        if frame_idx < appear_frames:
            t = frame_idx / appear_frames
            alpha = t  # 0 → 1

        # 阶段 2: 频闪 - 正弦波脉冲
        elif frame_idx < appear_frames + strobe_frames:
            elapsed = frame_idx - appear_frames
            # 频率随时间逐渐加快 (0.5 → 1.2 秒完成一个周期 → 0.15 秒)
            progress_in_strobe = elapsed / strobe_frames if strobe_frames > 0 else 0
            freq_hz = 3.0 + progress_in_strobe * 6.0  # 3Hz → 9Hz 加速
            # 正弦波: α 在 [0.25, 1.0] 之间振荡
            sin_val = math.sin(2 * math.pi * freq_hz * elapsed / self.video_fps)
            alpha = 0.25 + 0.75 * (0.5 + 0.5 * sin_val)

        # 阶段 3: 淡出
        else:
            t = (frame_idx - appear_frames - strobe_frames) / fade_frames
            alpha = max(0, 1.0 - t)

        if alpha <= 0.02:
            return frame

        # 绘制所有线段（发光效果）
        line_color = (230, 240, 255)  # BGR 浅青色
        for seg in self._segments:
            p1, p2 = seg["p1"], seg["p2"]
            # 外层光晕
            overlay = frame.copy()
            cv2.line(overlay, p1, p2, line_color, 5)
            cv2.addWeighted(overlay, alpha * 0.2, frame, 1 - alpha * 0.2, 0, frame)
            # 内层主线条
            overlay = frame.copy()
            cv2.line(overlay, p1, p2, line_color, 3)
            cv2.addWeighted(overlay, alpha * 0.5, frame, 1 - alpha * 0.5, 0, frame)
            # 核心亮线
            cv2.line(frame, p1, p2, (255, 255, 255), 1)

        # 绘制关键点（发光圆点）
        if alpha > 0.1:
            for pt in self._keypoints:
                # 外圈光晕
                overlay = frame.copy()
                cv2.circle(overlay, pt, 8, line_color, -1)
                cv2.addWeighted(overlay, alpha * 0.25, frame, 1 - alpha * 0.25, 0, frame)
                # 核心亮点
                cv2.circle(frame, pt, 4, (255, 255, 255), -1)

        return frame

    def _draw_persistent_court(self, frame: np.ndarray, alpha: float) -> None:
        """绘制持久低透明度球场叠加层（动画结束后的持续显示）"""
        if not self._segments:
            return
        base_color = (200, 220, 240)  # BGR 浅青色
        for seg in self._segments:
            p1, p2 = seg["p1"], seg["p2"]
            overlay = frame.copy()
            cv2.line(overlay, p1, p2, base_color, 3)
            cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
        # 关键点
        for pt in self._keypoints:
            overlay = frame.copy()
            cv2.circle(overlay, pt, 3, base_color, -1)
            cv2.addWeighted(overlay, alpha * 0.6, frame, 1 - alpha * 0.6, 0, frame)


# ============================================================
# SkeletonPass - 骨骼关键点
# ============================================================

_POSE_EDGES = [
    (0, 1), (0, 2), (1, 3), (2, 4),
    (5, 6),
    (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]
_NUM_KEYPOINTS = 17


class SkeletonPass(RenderPass):
    """骨骼关键点渲染层"""
    def __init__(self, player_keypoints: dict | None, video_fps: float):
        super().__init__()
        self.player_keypoints = player_keypoints
        self.video_fps = video_fps

    def draw(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        if self.player_keypoints is None:
            return frame
        frame_kps_list = self.player_keypoints.get("frame_keypoints", [])
        if not frame_kps_list:
            return frame
        if frame_idx >= len(frame_kps_list):
            kp_data = None
            for fk in reversed(frame_kps_list):
                if fk is not None:
                    kp_data = fk
                    break
            if kp_data is None:
                return frame
        else:
            kp_data = frame_kps_list[frame_idx]
            if kp_data is None:
                return frame
        keypoints = kp_data.get("keypoints", [])
        if not keypoints or len(keypoints) < _NUM_KEYPOINTS:
            return frame
        h, w = frame.shape[:2]
        skeleton_color = (0, 255, 200)
        for edge in _POSE_EDGES:
            i1, i2 = edge
            if i1 >= len(keypoints) or i2 >= len(keypoints):
                continue
            k1, k2 = keypoints[i1], keypoints[i2]
            if k1[2] < 0.2 or k2[2] < 0.2:
                continue
            x1, y1 = int(k1[0]), int(k1[1])
            x2, y2 = int(k2[0]), int(k2[1])
            if not (0 <= x1 < w and 0 <= y1 < h and 0 <= x2 < w and 0 <= y2 < h):
                continue
            overlay = frame.copy()
            cv2.line(overlay, (x1, y1), (x2, y2), (255, 255, 255), 5)
            cv2.addWeighted(overlay, 0.15, frame, 0.85, 0, frame)
            cv2.line(frame, (x1, y1), (x2, y2), skeleton_color, 2)
        for kp in keypoints:
            if kp[2] < 0.2:
                continue
            x, y = int(kp[0]), int(kp[1])
            if not (0 <= x < w and 0 <= y < h):
                continue
            overlay = frame.copy()
            cv2.circle(overlay, (x, y), 6, (255, 255, 255), -1)
            cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)
            cv2.circle(frame, (x, y), 4, skeleton_color, -1)
            cv2.circle(frame, (x, y), 2, (255, 255, 255), -1)
        return frame


# ============================================================
# MiniCourtPass - 迷你球场
# ============================================================

class MiniCourtPass(RenderPass):
    """迷你球场轨迹可视化渲染层"""
    def __init__(self, court_coordinates: dict | None,
                 ball_detections: list[dict[int, list[float]]],
                 ball_mini_coords: list[dict[int, tuple]] | None):
        super().__init__()
        self.court_coordinates = court_coordinates
        self.ball_detections = ball_detections
        self.ball_mini_coords = ball_mini_coords or []

    def draw(self, frame: np.ndarray, frame_idx: int) -> np.ndarray:
        h, w = frame.shape[:2]
        mini_court_kps = self.court_coordinates.get("mini_court_keypoints", []) if self.court_coordinates else []
        if not mini_court_kps or not self.ball_mini_coords:
            return frame
        card_w, card_h = 280, 340
        card_x1 = w - card_w - 15
        card_y1 = h - card_h - 15
        card_x2 = w - 15
        card_y2 = h - 15
        overlay = frame.copy()
        cv2.rectangle(overlay, (card_x1, card_y1), (card_x2, card_y2), (28, 28, 28), -1)
        cv2.rectangle(overlay, (card_x1, card_y1), (card_x2, card_y2), (80, 80, 80), 1)
        cv2.addWeighted(overlay, 0.62, frame, 0.38, 0, frame)
        cv2.putText(frame, "COURT VIEW", (card_x1 + 10, card_y1 + 22),
                    cv2.FONT_HERSHEY_DUPLEX, 0.5, (180, 180, 180), 1, cv2.LINE_AA)
        padding = 20
        court_draw_x1 = card_x1 + padding
        court_draw_y1 = card_y1 + 35
        court_draw_x2 = card_x2 - padding
        court_draw_y2 = card_y2 - padding
        court_draw_w = court_draw_x2 - court_draw_x1
        court_draw_h = court_draw_y2 - court_draw_y1
        if len(mini_court_kps) >= 28:
            pts = [(mini_court_kps[i], mini_court_kps[i + 1]) for i in range(0, 28, 2)]
            court_lines = [
                (0, 2), (4, 5), (6, 7), (1, 3),
                (0, 1), (8, 9), (10, 11), (2, 3),
                (4, 6), (12, 13),
            ]
            all_x = [p[0] for p in pts[:14]]
            all_y = [p[1] for p in pts[:14]]
            ref_min_x, ref_max_x = min(all_x), max(all_x)
            ref_min_y, ref_max_y = min(all_y), max(all_y)
            ref_w = max(ref_max_x - ref_min_x, 1)
            ref_h = max(ref_max_y - ref_min_y, 1)
            for line_pair in court_lines:
                p1 = pts[line_pair[0]]
                p2 = pts[line_pair[1]]
                if p1 and p2:
                    x1_n = int(court_draw_x1 + (p1[0] - ref_min_x) / ref_w * court_draw_w)
                    y1_n = int(court_draw_y1 + (p1[1] - ref_min_y) / ref_h * court_draw_h)
                    x2_n = int(court_draw_x1 + (p2[0] - ref_min_x) / ref_w * court_draw_w)
                    y2_n = int(court_draw_y1 + (p2[1] - ref_min_y) / ref_h * court_draw_h)
                    cv2.line(frame, (x1_n, y1_n), (x2_n, y2_n), (140, 140, 140), 1)
            if len(pts) > 13:
                p12, p13 = pts[12], pts[13]
                x12 = int(court_draw_x1 + (p12[0] - ref_min_x) / ref_w * court_draw_w)
                y12 = int(court_draw_y1 + (p12[1] - ref_min_y) / ref_h * court_draw_h)
                x13 = int(court_draw_x1 + (p13[0] - ref_min_x) / ref_w * court_draw_w)
                y13 = int(court_draw_y1 + (p13[1] - ref_min_y) / ref_h * court_draw_h)
                cv2.line(frame, (x12, y12), (x13, y13), (255, 200, 50), 2)
                cv2.putText(frame, "NET", (x12, y12 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 200, 50), 1)
            track_colors = [
                (0, 255, 255), (255, 0, 0), (0, 255, 0),
                (255, 0, 255), (255, 128, 0), (0, 128, 255),
            ]
            active_tracks: dict[int, list[tuple]] = {}
            lookback = min(60, frame_idx)
            for f_idx in range(max(0, frame_idx - lookback), frame_idx + 1):
                if f_idx < len(self.ball_mini_coords):
                    for tid, pos in self.ball_mini_coords[f_idx].items():
                        if pos and len(pos) >= 2 and pos != (0, 0):
                            if tid not in active_tracks:
                                active_tracks[tid] = []
                            active_tracks[tid].append(pos)
            for tid, coords in active_tracks.items():
                color = track_colors[int(tid) % len(track_colors)]
                for i in range(1, len(coords)):
                    p1_c = coords[i - 1]
                    p2_c = coords[i]
                    x1_m = int(court_draw_x1 + (p1_c[0] - ref_min_x) / ref_w * court_draw_w)
                    y1_m = int(court_draw_y1 + (p1_c[1] - ref_min_y) / ref_h * court_draw_h)
                    x2_m = int(court_draw_x1 + (p2_c[0] - ref_min_x) / ref_w * court_draw_w)
                    y2_m = int(court_draw_y1 + (p2_c[1] - ref_min_y) / ref_h * court_draw_h)
                    t = i / len(coords)
                    alpha_t = 0.3 + t * 0.7
                    overlay = frame.copy()
                    cv2.line(overlay, (x1_m, y1_m), (x2_m, y2_m), color, 2)
                    cv2.addWeighted(overlay, alpha_t * 0.5, frame, 1 - alpha_t * 0.5, 0, frame)
                if coords:
                    last = coords[-1]
                    lx = int(court_draw_x1 + (last[0] - ref_min_x) / ref_w * court_draw_w)
                    ly = int(court_draw_y1 + (last[1] - ref_min_y) / ref_h * court_draw_h)
                    cv2.circle(frame, (lx, ly), 5, color, -1)
                    cv2.circle(frame, (lx, ly), 3, (255, 255, 255), -1)
        return frame
