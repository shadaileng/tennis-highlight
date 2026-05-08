import gzip
import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str, float | None], None]

BALL_ALGORITHM = "ball_tracking_yolo"
BALL_ARTIFACT_VERSION = 1
BALL_TARGET_WIDTH = 640
DEFAULT_BALL_MODEL = "yolov8n.pt"
BALL_CONF_THRESHOLD = 0.25
IOU_TRACKING_THRESHOLD = 0.3


def analyze_ball_trajectory(
    analysis_id: str,
    video_path: Path,
    video_duration_s: float,
    sample_fps: float,
    range_start_s: float,
    range_end_s: float,
    progress_cb: ProgressCallback | None = None,
    model_name: str = DEFAULT_BALL_MODEL,
    conf_threshold: float = BALL_CONF_THRESHOLD,
    image_size: int = BALL_TARGET_WIDTH,
) -> tuple[Path, dict]:
    """Detect balls and generate trajectory data."""
    try:
        from ultralytics import YOLO
    except Exception as e:
        raise RuntimeError(
            "Ultralytics required. Install with `.venv/bin/pip install ultralytics`."
        ) from e

    from app.pipeline.motion_analysis import _probe_video
    info = _probe_video(video_path)
    target_w = int(image_size)
    target_h = max(2, ((info["height"] * target_w) // info["width"]) & ~1)
    duration = max(0.0, range_end_s - range_start_s)
    total_samples = max(1, int(round(duration * sample_fps)))

    if progress_cb:
        progress_cb(1.0, f"Loading ball model {model_name}", None)

    model = YOLO(model_name)

    if progress_cb:
        progress_cb(5.0, f"Detecting balls in {total_samples:,} samples", None)

    trajectory_points: list[dict] = []
    prev_ball = None
    frame_idx = 0
    started = time.monotonic()

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{range_start_s:.3f}",
        "-i", str(video_path),
        "-t", f"{duration:.3f}",
        "-an",
        "-vf", f"fps={sample_fps},scale={target_w}:{target_h}",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-",
    ]
    frame_bytes = target_w * target_h * 3

    with tempfile.TemporaryFile(mode="w+b") as stderr_tmp:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=stderr_tmp, bufsize=frame_bytes * 4
        )
        assert proc.stdout is not None
        try:
            while True:
                buf = _read_exact(proc.stdout, frame_bytes)
                if buf is None:
                    break

                frame = np.frombuffer(buf, dtype=np.uint8).reshape(target_h, target_w, 3)
                current_time = range_start_s + (frame_idx / sample_fps)

                results = model.predict(
                    frame,
                    conf=float(conf_threshold),
                    imgsz=int(image_size),
                    classes=[32],
                    verbose=False
                )[0]

                balls_in_frame = []
                if results.boxes and len(results.boxes) > 0:
                    for box in results.boxes:
                        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                        conf = float(box.conf[0])

                        cx = float(((x1 + x2) / 2) / target_w)
                        cy = float(((y1 + y2) / 2) / target_h)

                        balls_in_frame.append({
                            "x": cx,
                            "y": cy,
                            "confidence": conf,
                            "bbox": {
                                "x1": float(x1 / target_w),
                                "y1": float(y1 / target_h),
                                "x2": float(x2 / target_w),
                                "y2": float(y2 / target_h),
                            }
                        })

                current_ball = None
                if balls_in_frame:
                    if prev_ball is None:
                        current_ball = max(balls_in_frame, key=lambda b: b["confidence"])
                    else:
                        best_dist = float("inf")
                        for ball in balls_in_frame:
                            dist = np.sqrt(
                                (ball["x"] - prev_ball["x"])**2 +
                                (ball["y"] - prev_ball["y"])**2
                            )
                            if dist < best_dist:
                                best_dist = dist
                                current_ball = ball
                        if best_dist > IOU_TRACKING_THRESHOLD:
                            current_ball = None

                if current_ball:
                    trajectory_points.append({
                        "time_s": float(current_time),
                        "x": current_ball["x"],
                        "y": current_ball["y"],
                        "confidence": current_ball["confidence"],
                        "bbox": current_ball["bbox"],
                    })
                    prev_ball = {"x": current_ball["x"], "y": current_ball["y"]}
                else:
                    trajectory_points.append({
                        "time_s": float(current_time),
                        "x": None,
                        "y": None,
                        "confidence": 0.0,
                        "bbox": None,
                    })

                frame_idx += 1

                if progress_cb and (frame_idx % 10 == 0):
                    elapsed = time.monotonic() - started
                    rate = frame_idx / elapsed if elapsed > 0 else 0
                    remaining = max(0, total_samples - frame_idx)
                    eta = remaining / rate if rate > 0 else None
                    pct = 5.0 + (frame_idx / total_samples) * 90.0
                    progress_cb(
                        min(95.0, pct),
                        f"Detected balls {frame_idx:,}/{total_samples:,} — ETA {_fmt_eta(eta)}",
                        eta,
                    )

            proc.wait(timeout=30)

        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    metadata = {
        "detector": BALL_ALGORITHM,
        "detector_version": BALL_ARTIFACT_VERSION,
        "model_name": model_name,
        "conf_threshold": float(conf_threshold),
        "image_size": int(image_size),
        "duration_s": float(video_duration_s),
        "range_start_s": float(range_start_s),
        "range_end_s": float(range_end_s),
        "target_width": int(target_w),
        "target_height": int(target_h),
        "sample_fps": float(sample_fps),
        "sample_count": len(trajectory_points),
    }

    detected_count = sum(1 for p in trajectory_points if p["x"] is not None)
    avg_conf = float(np.mean([p["confidence"] for p in trajectory_points if p["x"] is not None])) if detected_count > 0 else 0
    summary = {
        "total_samples": len(trajectory_points),
        "detected_frames": detected_count,
        "detection_rate": (detected_count / len(trajectory_points) * 100) if trajectory_points else 0,
        "avg_confidence": avg_conf,
    }

    artifact = {
        "metadata": metadata,
        "summary": summary,
        "trajectory": trajectory_points,
    }

    from app.config import settings
    settings.analysis_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"
    with gzip.open(artifact_path, "wt", encoding="utf-8") as f:
        json.dump(artifact, f)

    if progress_cb:
        progress_cb(100.0, f"Done: {detected_count} ball detections", None)

    return artifact_path, artifact


def load_ball_artifact(artifact_path: Path) -> dict:
    with gzip.open(artifact_path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _read_exact(stream, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None if not buf else bytes(buf)
        buf.extend(chunk)
    return bytes(buf)


def _fmt_eta(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return f"{seconds:.0f}s"
    mins = seconds / 60
    if mins < 60:
        return f"{mins:.1f}m"
    return f"{mins / 60:.1f}h"
