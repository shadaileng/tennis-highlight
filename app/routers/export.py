from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.database import (
    create_export, get_export, get_video,
    list_exports_by_video_and_analysis, replace_segments,
)
from app.models import ExportRequest, ExportResponse, ExportStatusResponse
from app.pipeline.orchestrator import run_export

router = APIRouter()


@router.post("/export/{video_id}", response_model=ExportResponse)
async def start_export(
    video_id: str,
    payload: ExportRequest,
    background_tasks: BackgroundTasks,
) -> ExportResponse:
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
        segs.append({"start_s": start, "end_s": end, "is_on": s.is_on})
    segs.sort(key=lambda x: x["start_s"])

    if not any(s["is_on"] for s in segs):
        raise HTTPException(400, "No segments selected for export")

    await replace_segments(video_id, segs)

    analysis_id = payload.analysis_id
    export_id = await create_export(video_id, analysis_id)
    background_tasks.add_task(run_export, export_id, video_id, analysis_id, segs)
    return ExportResponse(export_id=export_id, status="pending")


@router.get("/export-status/{export_id}", response_model=ExportStatusResponse)
async def export_status(export_id: str) -> ExportStatusResponse:
    row = await get_export(export_id)
    if row is None:
        raise HTTPException(404, "export not found")
    return ExportStatusResponse(
        export_id=export_id,
        status=row["status"],
        error_msg=row["error_msg"],
        duration_s=row["duration_s"],
    )


@router.get("/exports-by-analysis/{analysis_id}")
async def exports_by_analysis(analysis_id: str, video_id: str = "") -> list[dict]:
    """获取指定分析记录的所有导出记录"""
    rows = await list_exports_by_video_and_analysis(video_id, analysis_id)
    return [
        {
            "export_id": r["id"],
            "status": r["status"],
            "duration_s": r["duration_s"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
