import asyncio
import json
import logging
from pathlib import Path

import subprocess

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.database import get_analysis_run, get_segments, get_segments_for_analysis, get_video, replace_segments
from app.models import Segment, SegmentsResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def segment_from_row(r) -> Segment:
    samples = None
    if "samples_json" in r.keys() and r["samples_json"]:
        try:
            samples = json.loads(r["samples_json"])
        except json.JSONDecodeError:
            samples = []
    return Segment(
        start_s=float(r["start_s"]),
        end_s=float(r["end_s"]),
        is_on=bool(r["is_on"]),
        source=r["source"] if "source" in r.keys() else "manual",
        raw_start_s=r["raw_start_s"] if "raw_start_s" in r.keys() else None,
        raw_end_s=r["raw_end_s"] if "raw_end_s" in r.keys() else None,
        avg_score=r["avg_score"] if "avg_score" in r.keys() else None,
        max_score=r["max_score"] if "max_score" in r.keys() else None,
        min_score=r["min_score"] if "min_score" in r.keys() else None,
        sample_count=r["sample_count"] if "sample_count" in r.keys() else None,
        decision_stage=r["decision_stage"] if "decision_stage" in r.keys() else None,
        samples=samples,
    )


@router.get("/segments/{video_id}", response_model=SegmentsResponse)
async def list_segments(video_id: str) -> SegmentsResponse:
    video = await get_video(video_id)
    if video is None:
        raise HTTPException(404, "video not found")
    rows = await get_segments(video_id)
    return SegmentsResponse(
        video_id=video_id,
        duration_s=float(video["duration_s"]),
        segments=[segment_from_row(r) for r in rows],
    )


@router.get("/analysis-segments/{analysis_id}", response_model=SegmentsResponse)
async def list_analysis_segments(analysis_id: str) -> SegmentsResponse:
    analysis = await get_analysis_run(analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")
    rows = await get_segments_for_analysis(analysis_id)
    return SegmentsResponse(
        video_id=analysis["video_id"],
        duration_s=float(analysis["duration_s"]),
        segments=[segment_from_row(r) for r in rows],
    )


@router.patch("/segments/{video_id}", response_model=SegmentsResponse)
async def save_segments(video_id: str, payload: SegmentsResponse) -> SegmentsResponse:
    video = await get_video(video_id)
    if video is None:
        raise HTTPException(404, "video not found")
    duration = float(video["duration_s"])

    segs = []
    for s in payload.segments:
        start = max(0.0, min(duration, float(s.start_s)))
        end = max(0.0, min(duration, float(s.end_s)))
        if end <= start:
            continue
        segs.append({
            "start_s": start,
            "end_s": end,
            "is_on": s.is_on,
            "source": s.source,
            "raw_start_s": s.raw_start_s,
            "raw_end_s": s.raw_end_s,
            "avg_score": s.avg_score,
            "max_score": s.max_score,
            "min_score": s.min_score,
            "sample_count": s.sample_count,
            "decision_stage": s.decision_stage,
            "samples": s.samples or [],
        })
    segs.sort(key=lambda x: x["start_s"])

    await replace_segments(video_id, segs)
    rows = await get_segments(video_id)
    return SegmentsResponse(
        video_id=video_id,
        duration_s=duration,
        segments=[segment_from_row(r) for r in rows],
    )


@router.get("/video-file/{video_id}")
async def video_file_path(video_id: str) -> dict:
    """Return the public URL of the video file for the player, transcoding to h.264 if needed."""
    video = await get_video(video_id)
    if video is None:
        raise HTTPException(404, "video not found")

    original = Path(video["filepath"])
    browser_path = original.parent / f"{original.stem}_browser.mp4"

    if not browser_path.exists():
        # 检查视频编码
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1", str(original)],
            capture_output=True, text=True, timeout=10,
        )
        codec = result.stdout.strip().lower()

        if codec and codec != "h264":
            logger.info("Transcoding %s from %s to h.264 for browser playback...", original.name, codec)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["ffmpeg", "-y", "-i", str(original),
                     "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                     "-c:a", "aac", "-movflags", "+faststart",
                     str(browser_path)],
                    capture_output=True, text=True, timeout=600,
                ),
            )
            logger.info("Transcoded %s → %s", original.name, browser_path.name)
        else:
            # 已经是 h.264，复制一份作为 browser 版本（跳过转码）
            if not browser_path.exists() and codec == "h264":
                import shutil
                shutil.copy2(original, browser_path)

    if browser_path.exists():
        name = browser_path.name
    else:
        name = original.name

    return {"url": f"/uploads/{name}"}


@router.get("/calibration-frame/{video_id}")
async def calibration_frame(video_id: str) -> Response:
    """提取视频首帧截图用于手动球场校准（跳过片头 1 秒）。"""
    video = await get_video(video_id)
    if video is None:
        raise HTTPException(404, "video not found")

    original = Path(video["filepath"])
    if not original.exists():
        raise HTTPException(404, "video file not found")

    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(original), "-ss", "1",
         "-vframes", "1", "-q:v", "2", "-f", "image2pipe", "-"],
        capture_output=True, timeout=60,
    )
    if result.returncode != 0 or not result.stdout:
        raise HTTPException(500, "failed to extract frame")

    return Response(content=result.stdout, media_type="image/jpeg")
