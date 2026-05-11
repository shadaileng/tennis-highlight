"""Quick test script to detect ball trajectory"""
from pathlib import Path
from app.pipeline.ball_tracking import analyze_ball_trajectory
import time

test_video = Path("data/uploads/input_video.mp4")
print(f"Video: {test_video}")
print(f"Exists: {test_video.exists()}")

if test_video.exists():
    analysis_id = f"test_{int(time.time())}"
    print(f"Analysis ID: {analysis_id}")

    def progress(p, m, e):
        print(f"  {p:.1f}% - {m}")

    # Use shorter range for faster testing
    artifact_path, artifact, video_output = analyze_ball_trajectory(
        analysis_id=analysis_id,
        video_path=test_video,
        video_duration_s=30.0,
        sample_fps=4.0,
        range_start_s=0.0,
        range_end_s=3.0,  # Only 3 seconds
        progress_cb=progress,
    )

    print(f"\n=== Analysis Results ===")
    print(f"Artifact: {artifact_path}")
    print(f"Trajectory video: {video_output}")
    print(f"Total samples: {artifact['summary']['total_samples']}")
    print(f"Detected frames: {artifact['summary']['detected_frames']}")
    print(f"Detection rate: {artifact['summary']['detection_rate']:.1f}%")
    print(f"Ball detections: {len(artifact['ball_detections'])} frames")

    # Show first 10 trajectory points
    print(f"\n=== Sample Trajectory Points ===")
    for i, det in enumerate(artifact['ball_detections'][:10]):
        if 1 in det:
            x1, y1, x2, y2 = det[1]
            print(f"  Frame {i}: ({x1:.0f}, {y1:.0f}, {x2:.0f}, {y2:.0f})")
