"""
视频渲染模块 - Render Pipeline 装配函数

使用 RenderPipeline 组合各渲染层，按 render_options 控制开关。
"""

import logging
import subprocess
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from app.pipeline.render_pipeline import (
    RenderPipeline,
    BallTrajectoryPass,
    SparkPass,
    BallSpeedPass,
    RunningSpeedPass,
    LandingMarkerPass,
    SkeletonPass,
    MiniCourtPass,
    CourtAnimationPass,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str, float | None], None]


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
    player_keypoints: dict | None = None,
    render_options: dict | None = None,
) -> Path:
    """
    在原始视频上使用 RenderPipeline 绘制可视化内容并输出 MP4

    render_options 控制各渲染层的启用开关：
        {
            "ball_trail": True,       # 网球轨迹
            "ball_speed": True,       # 球速文本
            "running_speed": True,    # 跑动速度连线
            "skeleton": True,         # 骨骼关键点
            "spark": True,            # 击球火花效果
            "court_anim": True,       # 开场一笔画成球场动画
        }
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # 使用 ffmpeg pipe 直接编码 H.264
    ffmpeg_cmd = [
        'ffmpeg', '-y',
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

    logger.info(f"Rendering: {width}x{height} @ {video_fps}fps, {total_frames} frames")

    # ---- 组装 RenderPipeline ----
    pipeline = RenderPipeline(width, height, video_fps)

    pipeline.add_pass(BallTrajectoryPass(ball_detections, width, height))
    pipeline.add_pass(SparkPass(hit_moments or [], video_fps))
    pipeline.add_pass(BallSpeedPass(speed_events, video_fps))
    pipeline.add_pass(RunningSpeedPass(speed_events, video_fps))
    pipeline.add_pass(LandingMarkerPass(
        ball_detections, video_fps,
        court_keypoints=court_coordinates.get("original_keypoints") if court_coordinates else None,
    ))
    pipeline.add_pass(SkeletonPass(player_keypoints, video_fps))
    pipeline.add_pass(CourtAnimationPass(court_coordinates, video_fps))
    pipeline.add_pass(MiniCourtPass(court_coordinates, ball_detections, ball_mini_coords))

    # 应用 render_options 控制开关
    if render_options:
        pipeline.get_pass("BallTrajectoryPass").enabled = render_options.get("ball_trail", True)
        pipeline.get_pass("SparkPass").enabled = render_options.get("spark", True)
        pipeline.get_pass("BallSpeedPass").enabled = render_options.get("ball_speed", True)
        pipeline.get_pass("RunningSpeedPass").enabled = render_options.get("running_speed", True)
        pipeline.get_pass("SkeletonPass").enabled = render_options.get("skeleton", True)
        pipeline.get_pass("CourtAnimationPass").enabled = render_options.get("court_anim", True)
        # LandingMarkerPass 和 MiniCourtPass 默认保持启用（无对应 checkbox）

    # 初始化所有启用的渲染层
    pipeline.initialize_all()

    # ---- 跳转到起始位置 ----
    start_frame = int(range_start_s * video_fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    try:
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # 使用 pipeline 逐帧渲染
            frame = pipeline.render_frame(frame, frame_idx)

            # 写入 ffmpeg stdin
            proc.stdin.write(frame.tobytes())
            frame_idx += 1

            if progress_cb and frame_idx % 30 == 0:
                pct = (frame_idx / total_frames) * 100 if total_frames > 0 else 0
                progress_cb(min(95.0, pct + 5.0), f"Rendering frame {frame_idx}/{total_frames}", None)

    finally:
        cap.release()
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed with return code {proc.returncode}")
        logger.info(f"Video saved to: {output_path}")

    return output_path


def render_ball_video_from_artifact(
    video_path: Path,
    artifact: dict,
    output_path: Path,
    progress_cb: ProgressCallback | None = None,
    render_options: dict | None = None,
) -> Path:
    """
    从 artifact 数据渲染带可视化内容的视频

    Args:
        video_path: 输入视频路径
        artifact: ball.json.gz 解析后的 artifact 数据
        output_path: 输出视频路径
        progress_cb: 进度回调
        render_options: 渲染选项开关
    """
    ball_detections = artifact.get("ball_detections", [])
    metadata = artifact.get("metadata", {})
    hit_moments = artifact.get("hit_moments", [])
    player_detections = artifact.get("player_detections", None)
    speed_events = artifact.get("speed_events", None)
    court_coordinates = artifact.get("court_coordinates", None)
    ball_mini_coords = artifact.get("ball_mini_coords", None)
    player_keypoints = artifact.get("player_keypoints", None)

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
        player_keypoints=player_keypoints,
        render_options=render_options,
    )
