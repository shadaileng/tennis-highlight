import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.database import complete_analysis, create_analysis, get_analysis_run, get_video, update_analysis_status
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

    async def run():
        try:
            await update_analysis_status(ball_analysis_id, "analyzing")
            artifact_path, _ = analyze_ball_trajectory(
                analysis_id=ball_analysis_id,
                video_path=video_path,
                video_duration_s=analysis["duration_s"],
                sample_fps=sample_fps,
                range_start_s=analysis["range_start_s"] or 0,
                range_end_s=analysis["range_end_s"] or analysis["duration_s"],
                progress_cb=None,
            )
            await complete_analysis(
                ball_analysis_id,
                artifact_path=str(artifact_path),
                instant_knobs_json=json.dumps({"sample_fps": req.sample_fps}),
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
    return {
        "trajectory": artifact["trajectory"],
        "summary": artifact["summary"],
        "metadata": artifact["metadata"],
    }


class BallFromVideoRequest(BaseModel):
    sample_fps: float = 4.0
    range_start_s: float | None = None
    range_end_s: float | None = None


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

    ball_analysis_id = await create_analysis(
        video_id=video_id,
        algorithm=BALL_ALGORITHM,
        noninstant_knobs_json=json.dumps({}),
        instant_knobs_json=json.dumps({"sample_fps": req.sample_fps}),
        range_start_s=range_start,
        range_end_s=range_end,
    )

    async def run():
        try:
            await update_analysis_status(ball_analysis_id, "analyzing")
            artifact_path, _ = analyze_ball_trajectory(
                analysis_id=ball_analysis_id,
                video_path=video_path,
                video_duration_s=duration,
                sample_fps=req.sample_fps,
                range_start_s=range_start,
                range_end_s=range_end,
                progress_cb=None,
            )
            await complete_analysis(
                ball_analysis_id,
                artifact_path=str(artifact_path),
                instant_knobs_json=json.dumps({"sample_fps": req.sample_fps}),
            )
        except Exception as e:
            logger.exception("Ball trajectory analysis failed")
            await update_analysis_status(ball_analysis_id, "error", str(e))

    background_tasks.add_task(run)
    return {"ball_analysis_id": ball_analysis_id, "status": "started"}
