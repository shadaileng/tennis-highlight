import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database import (
    complete_analysis,
    create_analysis,
    get_analysis_run,
    get_video,
    update_analysis_progress_sync,
    update_analysis_status,
)
from app.pipeline.ball_tracking import (
    BALL_ALGORITHM,
    analyze_ball_trajectory,
    load_ball_artifact,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/ball-trajectory/{analysis_id}")
async def start_ball_analysis(
    analysis_id: str,
    background_tasks: BackgroundTasks,
    sample_fps: float = 4.0,
):
    """Start ball trajectory analysis for an existing analysis."""
    analysis = await get_analysis_run(analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")

    ball_analysis_id = await create_analysis(
        video_id=analysis["video_id"],
        algorithm=BALL_ALGORITHM,
        noninstant_knobs_json=json.dumps({}),
        instant_knobs_json=json.dumps({"sample_fps": sample_fps}),
        range_start_s=analysis["range_start_s"] or 0,
        range_end_s=analysis["range_end_s"] or analysis["duration_s"],
    )

    video_path = Path(analysis["filepath"])
    if not video_path.exists():
        raise HTTPException(404, "video file not found")

    # 读取 court_calibration（如果有手动标记的球场）
    video = await get_video(analysis["video_id"])
    court_calibration = None
    if video and video["court_calibration_json"]:
        court_calibration = json.loads(video["court_calibration_json"])

    async def run():
        try:
            await update_analysis_status(ball_analysis_id, "analyzing")
            start_time = time.time()
            logger.info(f"[{ball_analysis_id}] Starting ball trajectory analysis...")

            def progress_cb(percent: float, message: str, eta_s: float | None = None) -> None:
                update_analysis_progress_sync(ball_analysis_id, percent, message, eta_s)

            loop = asyncio.get_running_loop()
            artifact_path, _, video_output_path = await loop.run_in_executor(
                None,
                lambda: analyze_ball_trajectory(
                    analysis_id=ball_analysis_id,
                    video_path=video_path,
                    video_duration_s=analysis["duration_s"],
                    sample_fps=sample_fps,
                    range_start_s=analysis["range_start_s"] or 0,
                    range_end_s=analysis["range_end_s"] or analysis["duration_s"],
                    progress_cb=progress_cb,
                    court_calibration=court_calibration,
                    render_video=False,
                ),
            )
            elapsed = time.time() - start_time
            logger.info(f"[{ball_analysis_id}] Ball trajectory analysis completed in {elapsed:.2f}s")
            await complete_analysis(
                ball_analysis_id,
                artifact_path=str(artifact_path),
                instant_knobs_json=json.dumps({"sample_fps": sample_fps}),
                video_path=str(video_output_path) if video_output_path else None,
            )
        except Exception as e:
            logger.exception("Ball trajectory analysis failed")
            await update_analysis_status(ball_analysis_id, "error", str(e))

    background_tasks.add_task(run)
    return {"ball_analysis_id": ball_analysis_id, "status": "started"}


@router.get("/ball-trajectory/{analysis_id}")
async def get_ball_trajectory(analysis_id: str):
    """Get ball trajectory data for an analysis."""
    artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"

    if not artifact_path.exists():
        return {
            "trajectory": [],
            "summary": {"total_samples": 0, "detected_frames": 0, "detection_rate": 0, "avg_confidence": 0},
            "metadata": {},
        }

    artifact = load_ball_artifact(artifact_path)
    metadata = artifact.get("metadata", {})

    # 验证必需的 metadata 字段
    if "video_width" not in metadata or "video_height" not in metadata:
        raise HTTPException(400, "Invalid artifact: missing video_width or video_height in metadata")

    # 从 ball_detections 转换为 trajectory 格式
    ball_detections = artifact.get("ball_detections", [])
    trajectory = []
    w = metadata["video_width"]
    h = metadata["video_height"]
    for i, det in enumerate(ball_detections):
        # 兼容字符串和整数类型的 key
        bbox = det.get(1) or det.get("1")
        if bbox is not None:
            x1, y1, x2, y2 = bbox
            trajectory.append({
                "time_s": metadata.get("range_start_s", 0) + i / metadata.get("sample_fps", 4),
                "x": (x1 + x2) / 2 / w,
                "y": (y1 + y2) / 2 / h,
                "bbox": {"x1": x1 / w, "y1": y1 / h, "x2": x2 / w, "y2": y2 / h},
            })

    return {
        "trajectory": trajectory,
        "summary": artifact.get("summary", {}),
        "metadata": metadata,
    }


@router.get("/analysis/{analysis_id}/render-data")
async def get_ball_render_data(analysis_id: str):
    """获取 ball tracking 分析的全部渲染数据，用于前端 canvas 绘制覆盖层。

    返回 artifact 中所有渲染所需数据（轨迹、击球、速度、球场、骨骼、迷你球场）。
    """
    artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"
    if not artifact_path.exists():
        raise HTTPException(404, "artifact not found for this analysis")

    artifact = load_ball_artifact(artifact_path)
    metadata = artifact.get("metadata", {})
    w = metadata.get("video_width", 1920)
    h = metadata.get("video_height", 1080)
    fps = metadata.get("video_fps", 30.0)

    # 遍历 ball_detections 生成归一化轨迹
    ball_detections = artifact.get("ball_detections", [])
    trajectory = []
    for i, det in enumerate(ball_detections):
        bbox = det.get(1) or det.get("1")
        if bbox is not None:
            x1, y1, x2, y2 = bbox
            trajectory.append({
                "time_s": metadata.get("range_start_s", 0) + i / fps,
                "x": (x1 + x2) / 2 / w,
                "y": (y1 + y2) / 2 / h,
                "bbox": {"x1": x1 / w, "y1": y1 / h, "x2": x2 / w, "y2": y2 / h},
            })

    # hit_moments — 转换球位归一化
    hit_moments = artifact.get("hit_moments", [])
    for hm in hit_moments:
        ball_pos = hm.get("ball_pos")
        if ball_pos and len(ball_pos) >= 2 and w and h:
            hm["x_norm"] = ball_pos[0] / w
            hm["y_norm"] = ball_pos[1] / h

    # speed_events — 球速/跑动速度归一化
    speed_events = artifact.get("speed_events", None)
    if speed_events:
        for bs in speed_events.get("ball_speeds", []):
            ball_pos = bs.get("ball_pos")
            if ball_pos and len(ball_pos) >= 2 and w and h:
                bs["x_norm"] = ball_pos[0] / w
                bs["y_norm"] = ball_pos[1] / h
            if "speed_ms" in bs and "speed_mps" not in bs:
                bs["speed_mps"] = bs["speed_ms"]
        for ps in speed_events.get("player_speeds", []):
            start_pos = ps.get("start_pos")
            end_pos = ps.get("end_pos")
            if start_pos and len(start_pos) >= 2 and w and h:
                ps["start_xn"] = start_pos[0] / w
                ps["start_yn"] = start_pos[1] / h
            if end_pos and len(end_pos) >= 2 and w and h:
                ps["end_xn"] = end_pos[0] / w
                ps["end_yn"] = end_pos[1] / h
            ps["frame_idx"] = ps.get("start_frame", 0)
            if "speed_ms" in ps and "speed_mps" not in ps:
                ps["speed_mps"] = ps["speed_ms"]

    player_keypoints = artifact.get("player_keypoints", None)
    court_coordinates = artifact.get("court_coordinates", None)
    ball_mini_coords = artifact.get("ball_mini_coords", [])

    return {
        "trajectory": trajectory,
        "hit_moments": hit_moments,
        "speed_events": speed_events,
        "player_keypoints": player_keypoints,
        "court_coordinates": court_coordinates,
        "ball_mini_coords": ball_mini_coords,
        "metadata": metadata,
        "summary": artifact.get("summary", {}),
    }


class BallFromVideoRequest(BaseModel):
    range_start_s: float | None = None
    range_end_s: float | None = None
    render_options: dict | None = None


@router.post("/ball-trajectory-from-video/{video_id}")
async def start_ball_analysis_from_video(
    video_id: str,
    background_tasks: BackgroundTasks,
    payload: BallFromVideoRequest | None = None,
):
    """Start ball trajectory analysis directly from a video (without requiring an existing analysis)."""
    video = await get_video(video_id)
    if video is None:
        raise HTTPException(404, "video not found")

    req = payload or BallFromVideoRequest()
    duration = float(video["duration_s"])
    range_start = req.range_start_s if req.range_start_s is not None else 0.0
    range_end = req.range_end_s if req.range_end_s is not None else duration
    
    video_path = Path(video["filepath"])
    if not video_path.exists():
        raise HTTPException(404, "video file not found")

    # 读取 court_calibration（如果有手动标记的球场）
    court_calibration = None
    if video["court_calibration_json"]:
        court_calibration = json.loads(video["court_calibration_json"])

    ball_analysis_id = await create_analysis(
        video_id=video_id,
        algorithm=BALL_ALGORITHM,
        noninstant_knobs_json=json.dumps({}),
        instant_knobs_json=json.dumps({}),
        range_start_s=range_start,
        range_end_s=range_end,
    )

    async def run():
        try:
            await update_analysis_status(ball_analysis_id, "analyzing")
            start_time = time.time()
            logger.info(f"[{ball_analysis_id}] Starting ball trajectory analysis...")

            def progress_cb(percent: float, message: str, eta_s: float | None = None) -> None:
                update_analysis_progress_sync(ball_analysis_id, percent, message, eta_s)

            loop = asyncio.get_running_loop()
            artifact_path, _, video_output_path = await loop.run_in_executor(
                None,
                lambda: analyze_ball_trajectory(
                    analysis_id=ball_analysis_id,
                    video_path=video_path,
                    video_duration_s=duration,
                    sample_fps=30.0,  # 传入视频帧率用于 metadata，实际逐帧检测
                    range_start_s=range_start,
                    range_end_s=range_end,
                    progress_cb=progress_cb,
                    render_options=req.render_options,
                    court_calibration=court_calibration,
                    render_video=False,
                ),
            )
            elapsed = time.time() - start_time
            logger.info(f"[{ball_analysis_id}] Ball trajectory analysis completed in {elapsed:.2f}s")
            await complete_analysis(
                ball_analysis_id,
                artifact_path=str(artifact_path),
                instant_knobs_json=json.dumps({}),
                video_path=str(video_output_path) if video_output_path else None,
            )
        except Exception as e:
            logger.exception("Ball trajectory analysis failed")
            await update_analysis_status(ball_analysis_id, "error", str(e))

    background_tasks.add_task(run)
    return {"ball_analysis_id": ball_analysis_id, "status": "started"}
