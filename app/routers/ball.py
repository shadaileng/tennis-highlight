import json
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.config import settings
from app.database import create_analysis, get_analysis_run, update_analysis_status
from app.pipeline.ball_tracking import (
    BALL_ALGORITHM,
    DEFAULT_BALL_MODEL,
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
    model_name: str = DEFAULT_BALL_MODEL,
    conf_threshold: float = 0.25,
):
    """Start ball trajectory analysis for an existing analysis."""
    analysis = await get_analysis_run(analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")

    ball_analysis_id = await create_analysis(
        video_id=analysis["video_id"],
        algorithm=BALL_ALGORITHM,
        noninstant_knobs_json=json.dumps({"model_name": model_name, "conf_threshold": conf_threshold}),
        instant_knobs_json=json.dumps({"sample_fps": sample_fps}),
        range_start_s=analysis["range_start_s"] or 0,
        range_end_s=analysis["range_end_s"] or analysis["duration_s"],
    )

    video_path = Path(analysis["filepath"])
    if not video_path.exists():
        raise HTTPException(404, "video file not found")

    async def run():
        try:
            analyze_ball_trajectory(
                analysis_id=ball_analysis_id,
                video_path=video_path,
                video_duration_s=analysis["duration_s"],
                sample_fps=sample_fps,
                range_start_s=analysis["range_start_s"] or 0,
                range_end_s=analysis["range_end_s"] or analysis["duration_s"],
                progress_cb=None,
                model_name=model_name,
                conf_threshold=conf_threshold,
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
