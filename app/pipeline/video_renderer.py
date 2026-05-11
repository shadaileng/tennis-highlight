"""
视频渲染模块 - 参考 Tennis_AI_mainland 项目实现

直接在原始视频帧上绘制网球轨迹。
参考: https://github.com/qchen34/Tennis_AI_mainland
"""

import logging
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str, float | None], None]


def draw_ball_trajectory_video(
    video_path: Path,
    ball_detections: list[dict[int, list[float]]],
    output_path: Path,
    range_start_s: float = 0.0,
    sample_fps: float = 4.0,
    progress_cb: ProgressCallback | None = None,
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

    try:
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

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

    return draw_ball_trajectory_video(
        video_path=video_path,
        ball_detections=ball_detections,
        output_path=output_path,
        range_start_s=metadata.get("range_start_s", 0.0),
        sample_fps=metadata.get("sample_fps", 4.0),
        progress_cb=progress_cb,
    )
