import asyncio
import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

from app.config import settings
from app.database import (
    complete_analysis,
    get_analysis_run,
    get_export,
    get_video,
    replace_segments,
    update_analysis_progress_sync,
    update_analysis_status,
    update_export,
)
from app.pipeline.ball_tracking import BALL_ALGORITHM, analyze_ball_trajectory
from app.pipeline.ffmpeg_utils import probe_duration
from app.pipeline.motion_analysis import (
    analyze_video_to_artifact,
    default_knobs,
    normalize_knobs,
)
from app.pipeline.pose_analysis import POSE_ALGORITHM, analyze_pose_to_artifact
from app.pipeline.video_editor import export_segments

logger = logging.getLogger(__name__)

# 独立的进程池，避免阻塞主事件循环
_process_pool: ProcessPoolExecutor | None = None


def _get_process_pool() -> ProcessPoolExecutor:
    global _process_pool
    if _process_pool is None:
        import multiprocessing
        # 使用 CPU 核心数，但最多 4 个进程
        max_workers = min(multiprocessing.cpu_count(), 4)
        _process_pool = ProcessPoolExecutor(max_workers=max_workers)
        logger.info(f"Created process pool with {max_workers} workers")
    return _process_pool


def _progress_cb(analysis_id: str, percent: float, message: str, eta_s: float | None = None) -> None:
    """模块级别的回调函数（可 pickle），通过 analysis_id 写入进度到数据库。"""
    update_analysis_progress_sync(analysis_id, percent, message, eta_s)


def _progress_cb_no_eta(analysis_id: str, percent: float, message: str) -> None:
    """适配 motion_analysis 的 2 参数回调（ProgressCallback = Callable[[float, str], None]）。"""
    update_analysis_progress_sync(analysis_id, percent, message, None)


async def run_analysis(analysis_id: str) -> None:
    """Background task: analyze a video for in-play segments and store them.
    
    Uses a separate process pool to avoid blocking the main event loop
    during CPU-intensive operations like YOLO inference.
    """
    started = time.monotonic()
    try:
        await update_analysis_status(analysis_id, "analyzing")
        analysis = await get_analysis_run(analysis_id)
        if analysis is None:
            raise RuntimeError("analysis not found")

        video_id = analysis["video_id"]
        video_path = Path(analysis["filepath"])
        duration = float(analysis["duration_s"])
        logger.info(
            "analysis start: analysis_id=%s video_id=%s file=%s duration=%.1fs",
            analysis_id, video_id, video_path.name, duration,
        )

        try:
            knobs = normalize_knobs(json.loads(analysis["instant_knobs_json"]), default_knobs())
        except Exception:
            knobs = default_knobs()
        try:
            config = json.loads(analysis["noninstant_knobs_json"] or "{}")
        except Exception:
            config = {}
        if analysis["algorithm"] == POSE_ALGORITHM:
            loop = asyncio.get_running_loop()
            artifact_path, timeline, analysis_result = await loop.run_in_executor(
                _get_process_pool(),
                partial(
                    analyze_pose_to_artifact,
                    analysis_id,
                    video_path,
                    duration,
                    float(knobs["sample_fps"]),
                    float(analysis["range_start_s"] or 0.0),
                    float(analysis["range_end_s"] or duration),
                    partial(_progress_cb, analysis_id),
                    str(config.get("pose_model", "yolo11n-pose.pt")),
                    float(config.get("pose_conf", 0.25)),
                    int(config.get("pose_imgsz", 640)),
                ),
            )
        elif analysis["algorithm"] == BALL_ALGORITHM:
            # Ball trajectory analysis - uses PROCESS POOL to avoid blocking
            # This is the main fix for preventing service lock-up during YOLO inference
            video = await get_video(video_id)
            court_calibration = None
            if video and video["court_calibration_json"]:
                court_calibration = json.loads(video["court_calibration_json"])
            loop = asyncio.get_running_loop()
            artifact_path, artifact, video_output_path = await loop.run_in_executor(
                _get_process_pool(),
                partial(
                    analyze_ball_trajectory,
                    analysis_id,
                    video_path,
                    duration,
                    float(knobs.get("sample_fps", 4.0)),
                    float(analysis["range_start_s"] or 0.0),
                    float(analysis["range_end_s"] or duration),
                    partial(_progress_cb, analysis_id),
                    None,  # render_options
                    court_calibration,
                ),
            )
            # Ball analysis doesn't produce segments, create a default full-video segment
            range_start = float(analysis["range_start_s"] or 0.0)
            range_end = float(analysis["range_end_s"] or duration)
            timeline = [{
                "start_s": range_start,
                "end_s": range_end,
                "is_on": True,
                "source": "ball_trajectory",
                "avg_score": 1.0,
            }]
            await replace_segments(video_id, timeline, analysis_id=analysis_id)

            # Mark as complete (including video_path for MP4 download)
            await complete_analysis(
                analysis_id,
                artifact_path=str(artifact_path),
                instant_knobs_json=json.dumps({"sample_fps": knobs.get("sample_fps", 4.0)}),
                video_path=str(video_output_path) if video_output_path else None,
            )
            # Log and return early since we already called complete_analysis
            elapsed = time.monotonic() - started
            logger.info(
                "ball trajectory analysis complete: analysis_id=%s video_path=%s elapsed=%.1fs",
                analysis_id, video_output_path, elapsed,
            )
            return
        else:
            court_calibration = None
            if analysis["algorithm"] == "median_court_roi":
                video = await get_video(video_id)
                if video is None or not video["court_calibration_json"]:
                    raise RuntimeError("court calibration is required for median_court_roi")
                court_calibration = json.loads(video["court_calibration_json"])
            loop = asyncio.get_running_loop()
            artifact_path, timeline, analysis_result = await loop.run_in_executor(
                _get_process_pool(),
                partial(
                    analyze_video_to_artifact,
                    analysis_id,
                    video_path,
                    duration,
                    partial(_progress_cb_no_eta, analysis_id),
                    knobs,
                    analysis["algorithm"],
                    court_calibration,
                ),
            )
        summary = analysis_result.get("summary", analysis_result)

        await replace_segments(video_id, timeline, analysis_id=analysis_id)
        await complete_analysis(
            analysis_id,
            artifact_path=str(artifact_path),
            instant_knobs_json=json.dumps(analysis_result.get("config") or analysis_result.get("knobs") or {}),
        )
        elapsed = time.monotonic() - started
        logger.info(
            "analysis complete: analysis_id=%s in_play_segments=%d total_segments=%d elapsed=%.1fs",
            analysis_id, summary.get("on_count", 0), len(timeline), elapsed,
        )
    except Exception as e:
        logger.exception("analysis failed for %s", analysis_id)
        await update_analysis_status(analysis_id, "error", error_msg=str(e))


async def run_export(export_id: str, video_id: str, segments: list[dict]) -> None:
    """Background task: stitch user-selected segments into a final video."""
    started = time.monotonic()
    try:
        await update_export(export_id, status="exporting")
        video = await get_video(video_id)
        if video is None:
            raise RuntimeError("video not found")

        on_pairs = [
            (float(s["start_s"]), float(s["end_s"]))
            for s in segments
            if s.get("is_on") and float(s["end_s"]) > float(s["start_s"])
        ]
        if not on_pairs:
            raise RuntimeError("no segments selected for export")

        total_kept = sum(e - s for s, e in on_pairs)
        logger.info(
            "export start: export_id=%s video_id=%s kept_segments=%d kept_duration=%.1fs",
            export_id, video_id, len(on_pairs), total_kept,
        )

        output_path = settings.exports_dir / f"{export_id}.mp4"
        await export_segments(
            Path(video["filepath"]), on_pairs, output_path, export_id
        )

        duration_s = await probe_duration(output_path)
        await update_export(
            export_id,
            status="done",
            output_filepath=str(output_path),
            duration_s=duration_s,
        )
        elapsed = time.monotonic() - started
        logger.info(
            "export complete: export_id=%s output=%s duration=%.1fs elapsed=%.1fs",
            export_id, output_path.name, duration_s, elapsed,
        )
    except Exception as e:
        logger.exception("export failed for %s", export_id)
        await update_export(export_id, status="error", error_msg=str(e))
