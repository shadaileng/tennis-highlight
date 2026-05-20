# Tennis Highlight Extractor

A web application that automatically removes dead time from amateur tennis practice videos and provides **advanced ball/player trajectory analysis**.

Upload footage from a fixed tripod camera, the app uses motion analysis to detect in-play vs dead-time segments. Review the proposal in an interactive editor — toggle individual segments, nudge boundaries, preview each one — then export a single trimmed MP4 with only the rallies you kept.

Beyond highlight extraction, the app also supports **YOLO-based tennis ball detection**, **court calibration via homography**, **ball speed / player running speed calculation**, and a **composable render pipeline** that draws trajectory lines, spark effects, speed labels, skeleton overlays, and a mini-court map on the output video.

---

## Features

- **Motion-based highlight extraction** — median-frame background subtraction detects in-play segments
- **Interactive segment editor** — toggle, preview, and adjust segment boundaries in the browser
- **Court calibration** — manual 4-corner selection → Homography → 14 standard keypoints
- **Ball trajectory tracking** — YOLOv5 detection + interpolation + stroke detection
- **Ball speed display** — shows stroke speed (m/s) near the player
- **Player running speed** — speed between strokes with connecting lines on court
- **Player skeleton overlay** — YOLO11-pose per-frame skeleton rendering
- **Ball landing markers** — impact shockwave animation at landing points
- **Mini-court map** — bird's-eye view of the court in the corner
- **Composable render pipeline** — 8 independent render passes, each can be toggled on/off
- **Multi-ball tracking** — tracks individual ball IDs with distinct colors
- **Export** — trim highlights into a single MP4, or download trajectory-rendered video

---

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| **Python** | 3.12+ | Developed on 3.14 |
| **ffmpeg** | latest | Must be on `PATH` |
| **Node** | 18+ | Optional — only for frontend DOM tests |

---

## Setup

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# (Optional) Install Node dependency for DOM tests
npm install --no-save jsdom
```

### ML Models

Download the following model files into `models/`:

| Model | File | Size | Purpose |
|---|---|---|---|
| YOLOv5 | `yolo5_last.pt` | ~165 MB | Tennis ball detection |
| ResNet50 | `keypoints_model.pth` | ~90 MB | Court keypoint detection (fallback) |
| YOLO11-pose | `yolo11n-pose.pt` | ~6 MB | Player skeleton pose |
| YOLOv8 | `yolov8n.pt` | ~6 MB | Player detection & tracking |

---

## Run the server

```bash
uvicorn app.main:app --port 8000
# then open http://localhost:8000

# With hot reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Enable debug logs
VERBOSE=true uvicorn app.main:app --port 8000
```

Or use the management script:

```bash
bash scripts/restart.sh start
bash scripts/restart.sh stop
bash scripts/restart.sh status
bash scripts/restart.sh logs
```

---

## Run the tests

```bash
bash scripts/test.sh
```

This runs:
1. **Python tests** (`unittest`) — pipeline tests, schema contracts, ~23 tests
2. **Frontend DOM tests** (Node + `jsdom`) — runtime UI tests, ~14 tests

DOM tests are auto-skipped if `jsdom` isn't installed.

Run suites individually:

```bash
.venv/bin/python -m unittest discover tests/ -v   # Python only
node tests/test_frontend_render.js                # Frontend DOM only
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | SPA landing page |
| `POST` | `/upload` | Upload video (dedup by hash) |
| `GET` | `/videos` | List all videos |
| `GET` | `/media` | List all media assets |
| `GET` | `/status/{id}` | Video processing status |
| `DELETE` | `/videos/{id}` | Delete video (cascade) |
| `POST` | `/process/{id}` | Start analysis (algorithm, params, time range) |
| `GET` | `/analyses` | List all analyses |
| `GET` | `/analysis-status/{id}` | Analysis status |
| `DELETE` | `/analyses/{id}` | Delete analysis |
| `GET` | `/analysis/{id}` | Analysis details with segments |
| `POST` | `/analysis/{id}/preview` | Preview with adjusted parameters |
| `GET` | `/analysis/{id}/ball-video` | Download trajectory-rendered video |
| `GET` | `/analysis-segments/{id}` | Get analysis segments |
| `GET` | `/segments/{id}` | Get video segments |
| `PATCH` | `/segments/{id}` | Save segment revisions |
| `GET` | `/video-file/{id}` | Get video URL (auto-transcoded H.264) |
| `GET` | `/calibration-frame/{id}` | Extract first frame for court calibration |
| `PUT` | `/media/{id}/court-calibration` | Save 4-corner court calibration |
| `POST` | `/ball-trajectory/{id}` | Start ball trajectory analysis |
| `POST` | `/ball-trajectory-from-video/{id}` | Start trajectory from video directly |
| `GET` | `/ball-trajectory/{id}` | Get trajectory data |
| `GET` | `/pose-data/{id}` | Get pose skeleton data |
| `POST` | `/export/{id}` | Start export |
| `GET` | `/export-status/{id}` | Export status |
| `GET` | `/download/{id}` | Download exported video |

---

## Detection Algorithms

| Algorithm | Method | Use Case |
|---|---|---|
| `median_frame` | Per-pixel median across video → "empty court" background, then subtract | Default for fixed tripod. No warmup needed. |
| `median_court_roi` | Same as above, but weights foreground inside/outside court polygon | When people walk past camera causing false positives |
| `pose_skeleton_yolo` | YOLO11-pose frame-by-frame skeleton detection | When motion misses small/distant players |
| `ball_tracking_yolo` | YOLOv5 ball detection + interpolation + stroke detection | Advanced: trajectory, speed, landing |

---

## Render Pipeline

8 composable render passes, each independently toggleable via API parameters:

| Pass | Renders | Default |
|---|---|---|
| `BallTrajectoryPass` | Ball trajectory trail + detection boxes | On |
| `SparkPass` | Spark diamond effect at strokes | On |
| `BallSpeedPass` | Speed text "Pn XX m/s" near player | On |
| `RunningSpeedPass` | Running speed line + text between strokes | On |
| `SkeletonPass` | Cyan skeleton keypoint overlay | On |
| `LandingMarkerPass` | Yellow cross marker + shockwave at landing points | On |
| `CourtAnimationPass` | 1.5s court-outline draw animation at start | On |
| `MiniCourtPass` | Bird's-eye mini-court (280×340px) at bottom-right | On |

---

## Configuration

Settings are managed via `pydantic-settings` (file: `app/config.py`). Override with `.env` or environment variables:

| Key | Default | Description |
|---|---|---|
| `upload_dir` | `data/uploads` | Upload directory |
| `exports_dir` | `data/exports` | Export output directory |
| `analysis_dir` | `data/analysis` | Analysis artifact directory |
| `ball_videos_dir` | `data/ball_videos` | Rendered ball trajectory videos |
| `db_path` | `data/app.db` | SQLite database path |
| `motion_threshold` | `0.02` | Motion score threshold |
| `merge_gap_s` | `3.0` | Segment merge gap (seconds) |
| `min_segment_s` | `5.0` | Minimum segment duration |
| `segment_padding_s` | `1.0` | Segment edge padding |
| `sample_fps` | `2.0` | Sampling frame rate for analysis |
| `diff_threshold` | `25` | Background subtraction threshold |
| `median_bg_samples` | `80` | Frames sampled for median background |
| `progress_report_interval_s` | `5.0` | Progress callback interval |

---

## Project Layout

```
app/
  main.py              # FastAPI entry point
  config.py            # pydantic-settings
  database.py          # SQLite CRUD
  models.py            # Pydantic request/response models
  routers/             # HTTP endpoints
    upload.py          # POST /upload
    videos.py          # GET /videos, /media, PUT court-calibration, DELETE
    process.py         # POST /process, GET /analyses, /analysis-status
    status.py          # GET /status/{id}
    analysis.py        # GET /analysis/{id}, /analysis/{id}/preview, ball-video
    segments.py        # GET/PATCH /segments/{id}
    ball.py            # POST /ball-trajectory, /ball-trajectory-from-video
    pose.py            # GET /pose-data/{id}
    export.py          # POST /export/{id}
    download.py        # GET /download/{id}
  pipeline/
    motion_analysis.py    # Median-frame background subtraction
    ball_tracking.py      # YOLOv5 ball detection + trajectory + speed
    ball_tracker.py       # BallTracker class wrapper
    pose_analysis.py      # YOLO11-pose skeleton detection
    court_homography.py   # Manual 4-corner → Homography → 14 keypoints
    court_line_detector.py# ResNet50 automatic court detection (fallback)
    player_tracker.py     # YOLOv8 player detection & tracking
    racket_tracking.py    # Racket detection
    mini_court.py         # Pixel↔meter court coordinate mapping
    render_pipeline.py    # Composable RenderPipeline orchestrator
    video_renderer.py     # Pipeline assembly + ffmpeg pipe output
    video_editor.py       # ffmpeg extract + concat
    ffmpeg_utils.py       # ffmpeg/ffprobe utilities
    orchestrator.py       # Background task scheduler (ProcessPoolExecutor)
static/
  index.html           # Single-page UI
  app.js               # All frontend logic (~48 KB)
  style.css            # Stylesheet
tests/                 # Python + Node tests
scripts/
  test.sh              # Run all tests
  restart.sh           # Service management
  clear_analysis.sh    # Clear analysis data
data/                  # Runtime data (gitignored)
  uploads/             # Raw uploaded videos
  analysis/            # Artifacts (*.ball.json.gz)
  ball_videos/         # Trajectory-rendered MP4s
  exports/             # Clipped exports
  segments_tmp/        # Temp segment cache
  app.db               # SQLite database
models/                # ML model weights (gitignored)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.12+) |
| Web Server | Uvicorn |
| Video Processing | OpenCV, ffmpeg/ffprobe |
| ML Inference | Ultralytics (YOLOv5/v8/11), ResNet50 |
| Data Processing | NumPy, Pandas, SciPy |
| Database | SQLite |
| Frontend | Vanilla JS + HTML + CSS (no framework) |
| Testing | Python `unittest` + Node `jsdom` |

---

## Environment Variables

- `VERBOSE=true` — enable DEBUG-level logging
- `.env` file — override any setting in `app/config.py`

---

## Docker

Currently no Dockerfile — see [Setup](#setup) for local installation instructions.
