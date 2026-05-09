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

BALL_ALGORITHM = "ball_tracking_color"
BALL_ARTIFACT_VERSION = 2
BALL_TARGET_WIDTH = 640

# Tennis ball color detection thresholds (HSV)
# Yellow-green tennis ball: H: 25-70, S: 100-255, V: 100-255
HUE_MIN, HUE_MAX = 20, 70
SAT_MIN, SAT_MAX = 80, 255
VAL_MIN, VAL_MAX = 100, 255

# Ball size constraints (relative to frame)
MIN_BALL_AREA_RATIO = 0.00005
MAX_BALL_AREA_RATIO = 0.003
TRACKING_DISTANCE_THRESHOLD = 0.15


def _detect_tennis_ball_hsv(
    frame: np.ndarray,
    prev_pos: tuple[float, float] | None = None
) -> tuple[float, float, float] | None:
    """Detect tennis ball using HSV color segmentation.
    
    Returns: (center_x, center_y, confidence) or None if no ball found
    center_x, center_y are normalized to [0, 1]
    """
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # Create mask for tennis ball color (yellow-green)
    lower = np.array([HUE_MIN, SAT_MIN, VAL_MIN])
    upper = np.array([HUE_MAX, SAT_MAX, VAL_MAX])
    mask = cv2.inRange(hsv, lower, upper)
    
    # Morphological operations to reduce noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.erode(mask, kernel, iterations=1)
    mask = cv2.dilate(mask, kernel, iterations=2)
    
    # Find contours
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if not contours:
        return None
    
    max_area = 0
    best_ball = None
    
    for cnt in contours:
        area = cv2.contourArea(cnt)
        min_area = h * w * MIN_BALL_AREA_RATIO
        max_area_limit = h * w * MAX_BALL_AREA_RATIO
        
        if area < min_area or area > max_area_limit:
            continue
        
        # Get bounding box
        x, y, bw, bh = cv2.boundingRect(cnt)
        
        # Check circularity (tennis balls are round)
        perimeter = cv2.arcLength(cnt, True)
        if perimeter < 1:
            continue
        circularity = 4 * np.pi * area / (perimeter * perimeter)
        if circularity < 0.3:  # Not round enough
            continue
        
        # Prefer larger, more circular detections
        score = area * circularity
        
        if score > max_area:
            max_area = score
            cx = (x + bw / 2) / w
            cy = (y + bh / 2) / h
            # Confidence based on size and circularity
            conf = min(1.0, circularity * 0.7 + min(area / (h * w * 0.001), 1.0) * 0.3)
            best_ball = (cx, cy, conf)
    
    # If we have a previous position, prefer detections near it
    if best_ball and prev_pos:
        prev_x, prev_y = prev_pos
        cx, cy, conf = best_ball
        dist = np.sqrt((cx - prev_x)**2 + (cy - prev_y)**2)
        if dist > TRACKING_DISTANCE_THRESHOLD:
            # Too far from previous, might be false positive
            return None
        best_ball = (cx, cy, min(conf, 1.0 - dist))
    
    return best_ball


def analyze_ball_trajectory(
    analysis_id: str,
    video_path: Path,
    video_duration_s: float,
    sample_fps: float,
    range_start_s: float,
    range_end_s: float,
    progress_cb: ProgressCallback | None = None,
    image_size: int = BALL_TARGET_WIDTH,
) -> tuple[Path, dict]:
    """Detect tennis balls using color segmentation and generate trajectory data."""
    from app.pipeline.motion_analysis import _probe_video
    info = _probe_video(video_path)
    target_w = int(image_size)
    target_h = max(2, ((info["height"] * target_w) // info["width"]) & ~1)
    duration = max(0.0, range_end_s - range_start_s)
    total_samples = max(1, int(round(duration * sample_fps)))

    if progress_cb:
        progress_cb(1.0, "Initializing ball detection", None)

    if progress_cb:
        progress_cb(5.0, f"Detecting balls in {total_samples:,} samples", None)

    trajectory_points: list[dict] = []
    prev_ball_pos: tuple[float, float] | None = None
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

                # Detect tennis ball using color
                ball_det = _detect_tennis_ball_hsv(frame, prev_ball_pos)

                if ball_det:
                    cx, cy, conf = ball_det
                    trajectory_points.append({
                        "time_s": float(current_time),
                        "x": cx,
                        "y": cy,
                        "confidence": conf,
                        "bbox": {
                            "x1": float(cx - 0.01),
                            "y1": float(cy - 0.01),
                            "x2": float(cx + 0.01),
                            "y2": float(cy + 0.01),
                        },
                    })
                    prev_ball_pos = (cx, cy)
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
    detected_points = [p for p in trajectory_points if p["x"] is not None]
    avg_conf = float(np.mean([p["confidence"] for p in detected_points])) if detected_points else 0
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
