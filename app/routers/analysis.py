import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.database import (
    get_analysis_run,
    get_segments_for_analysis,
    replace_segments,
    update_analysis_knobs,
)
from app.models import (
    AnalysisMetadata,
    AnalysisPreviewRequest,
    AnalysisPreviewResponse,
    AnalysisResponse,
    DetectorKnobs,
    Segment,
)
from app.pipeline.ball_tracking import BALL_ALGORITHM, load_ball_artifact
from app.pipeline.motion_analysis import default_knobs, load_artifact, normalize_knobs, recompute_from_artifact
from app.pipeline.pose_analysis import POSE_ALGORITHM, load_pose_artifact
from app.routers.segments import segment_from_row

router = APIRouter()


def _knobs_model(knobs: dict) -> DetectorKnobs:
    return DetectorKnobs(**normalize_knobs(knobs))


def _metadata_model(metadata: dict, analysis_id: str | None = None) -> AnalysisMetadata:
    # 提供默认值以兼容 ball_tracking 等不同类型的分析
    return AnalysisMetadata(
        analysis_id=analysis_id,
        detector=metadata.get("detector", "unknown"),
        detector_version=int(metadata.get("detector_version", 1)),
        duration_s=float(metadata.get("duration_s", 0)),
        target_width=int(metadata.get("target_width", metadata.get("video_width", 1920))),
        target_height=int(metadata.get("target_height", metadata.get("video_height", 1080))),
        sample_fps=float(metadata.get("sample_fps", metadata.get("video_fps", 30))),
        median_bg_samples=int(metadata.get("median_bg_samples", 0)),
        sample_count=int(metadata.get("sample_count", 0)),
    )


def _segment_models(segments: list[dict]) -> list[Segment]:
    return [Segment(**seg) for seg in segments]


def _artifact_path(analysis) -> Path:
    path = analysis["artifact_path"]
    if not path:
        raise HTTPException(409, "analysis artifact missing; wait for this analysis to finish")
    artifact_path = Path(path)
    if not artifact_path.exists():
        raise HTTPException(410, "analysis artifact file missing; run the analysis again")
    return artifact_path


def _current_knobs(analysis, defaults: dict) -> dict:
    if analysis["instant_knobs_json"]:
        try:
            return normalize_knobs(json.loads(analysis["instant_knobs_json"]), defaults)
        except json.JSONDecodeError:
            pass
    return normalize_knobs(defaults, defaults)


def _lock_reanalysis_only(knobs: dict, metadata: dict) -> dict:
    locked = dict(knobs)
    locked["sample_fps"] = float(metadata["sample_fps"])
    locked["median_bg_samples"] = int(metadata["median_bg_samples"])
    return locked


@router.get("/analysis/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(analysis_id: str) -> AnalysisResponse:
    analysis = await get_analysis_run(analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")

    metadata = None
    defaults = default_knobs()
    summary = {
        "sample_count": 0,
        "on_count": 0,
        "total_count": 0,
        "on_duration_s": 0.0,
        "on_percent": 0.0,
        "foreground_percentiles": {},
        "smoothed_percentiles": {},
        "changed_segment_count": None,
    }

    if analysis["artifact_path"]:
        artifact_path = _artifact_path(analysis)
        if analysis["algorithm"] == POSE_ALGORITHM:
            artifact = load_pose_artifact(artifact_path)
            metadata = {
                **artifact["metadata"],
                "median_bg_samples": 0,
            }
            defaults = normalize_knobs(defaults, defaults)
            knobs = _current_knobs(analysis, defaults)
            summary = artifact.get("summary", {})
            summary["config"] = artifact.get("metadata", {})
        elif analysis["algorithm"] == BALL_ALGORITHM:
            # 网球检测使用 JSON 格式的 artifact
            artifact = load_ball_artifact(artifact_path)
            metadata = artifact.get("metadata", {})
            summary = artifact.get("summary", {})
            knobs = {}
            # Ball metadata doesn't have median_bg_samples, set default
            if "median_bg_samples" not in metadata:
                metadata["median_bg_samples"] = 0
        else:
            artifact = load_artifact(artifact_path)
            metadata = artifact["metadata"]
            defaults = normalize_knobs(metadata.get("default_knobs") or defaults, defaults)
            knobs = _current_knobs(analysis, defaults)
            result = recompute_from_artifact(artifact_path, knobs, include_samples=False)
            summary = result["summary"]
    else:
        if analysis["instant_knobs_json"]:
            try:
                knobs = normalize_knobs(json.loads(analysis["instant_knobs_json"]), defaults)
            except json.JSONDecodeError:
                knobs = normalize_knobs(defaults, defaults)
        else:
            knobs = normalize_knobs(defaults, defaults)

    return AnalysisResponse(
        video_id=analysis["video_id"],
        analysis_id=analysis_id,
        duration_s=float(analysis["duration_s"]),
        defaults=_knobs_model(defaults),
        knobs=_knobs_model(knobs),
        metadata=_metadata_model(metadata, analysis_id) if metadata else None,
        summary=summary,
    )


@router.post("/analysis/{analysis_id}/preview", response_model=AnalysisPreviewResponse)
async def preview_analysis(analysis_id: str, payload: AnalysisPreviewRequest) -> AnalysisPreviewResponse:
    analysis = await get_analysis_run(analysis_id)
    if analysis is None:
        raise HTTPException(404, "analysis not found")
    artifact_path = _artifact_path(analysis)
    if analysis["algorithm"] == POSE_ALGORITHM or analysis["algorithm"] == BALL_ALGORITHM:
        raise HTTPException(400, f"{analysis['algorithm']} does not support instant segment preview yet")
    metadata = load_artifact(artifact_path)["metadata"]

    requested = payload.knobs.model_dump()
    requested = _lock_reanalysis_only(requested, metadata)
    result = recompute_from_artifact(artifact_path, requested, include_samples=True)

    previous_rows = await get_segments_for_analysis(analysis_id)
    previous = [(round(float(r["start_s"]), 2), round(float(r["end_s"]), 2), bool(r["is_on"])) for r in previous_rows]
    current = [
        (round(float(s["start_s"]), 2), round(float(s["end_s"]), 2), bool(s["is_on"]))
        for s in result["segments"]
    ]
    changed_count = sum(1 for a, b in zip(previous, current) if a != b) + abs(len(previous) - len(current))
    result["summary"]["changed_segment_count"] = int(changed_count)

    await replace_segments(analysis["video_id"], result["segments"], analysis_id=analysis_id)
    await update_analysis_knobs(analysis_id, json.dumps(result["knobs"]))

    rows = await get_segments_for_analysis(analysis_id)
    return AnalysisPreviewResponse(
        video_id=analysis["video_id"],
        analysis_id=analysis_id,
        duration_s=float(analysis["duration_s"]),
        knobs=_knobs_model(result["knobs"]),
        segments=[segment_from_row(r) for r in rows],
        summary=result["summary"],
    )


@router.get("/analysis/{analysis_id}/ball-video")
async def download_ball_video(analysis_id: str):
    """下载带网球轨迹的 MP4 视频

    Args:
        analysis_id: 分析 ID

    Returns:
        MP4 视频文件

    Raises:
        HTTPException: 404 if analysis not found or video not generated
    """
    analysis = await get_analysis_run(analysis_id)
    if analysis is None:
        raise HTTPException(404, "Analysis not found")

    # 检查是否为网球检测算法
    if analysis["algorithm"] != BALL_ALGORITHM:
        raise HTTPException(400, "This endpoint is only available for ball trajectory analysis")

    # 检查视频路径
    video_path = analysis["video_path"] if analysis["video_path"] else None
    if not video_path:
        raise HTTPException(404, "Ball trajectory video not generated yet")

    video_file = Path(video_path)
    if not video_file.exists():
        raise HTTPException(410, "Ball trajectory video file missing")

    return FileResponse(
        video_file,
        media_type="video/mp4",
        filename=f"ball_trajectory_{analysis_id}.mp4",
    )
