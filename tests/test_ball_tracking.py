import os
import shutil
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from app.pipeline.ball_tracking import (
    BALL_ALGORITHM,
    BALL_ARTIFACT_VERSION,
    HUE_MAX,
    HUE_MIN,
    MAX_BALL_AREA_RATIO,
    MIN_BALL_AREA_RATIO,
    SAT_MAX,
    SAT_MIN,
    VAL_MAX,
    VAL_MIN,
    _detect_tennis_ball_hsv,
    analyze_ball_trajectory,
    load_ball_artifact,
)


class BallDetectionUnitTests(unittest.TestCase):
    """Unit tests for the ball detection algorithm."""

    def test_detect_yellow_ball(self):
        """Test detection of a tennis ball (yellow-green) in a frame."""
        # Create a frame with a tennis ball (yellow-green color)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Tennis ball color: H=30-60, S=150-255, V=150-255
        ball_x, ball_y, ball_r = 320, 240, 15
        cv2.circle(frame, (ball_x, ball_y), ball_r, (50, 200, 220), -1)

        result = _detect_tennis_ball_hsv(frame, None)

        self.assertIsNotNone(result)
        cx, cy, conf = result
        # Center should be near the ball position (normalized)
        self.assertAlmostEqual(cx, ball_x / 640, delta=0.1)
        self.assertAlmostEqual(cy, ball_y / 480, delta=0.1)
        self.assertGreater(conf, 0.0)

    def test_detect_no_ball(self):
        """Test that no detection occurs when there's no tennis ball."""
        # Create a blue frame (no tennis ball color)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:] = (128, 50, 30)  # BGR: blue-ish

        result = _detect_tennis_ball_hsv(frame, None)

        self.assertIsNone(result)

    def test_detect_with_tracking(self):
        """Test ball tracking with previous position."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        ball_x, ball_y, ball_r = 320, 240, 15
        cv2.circle(frame, (ball_x, ball_y), ball_r, (50, 200, 220), -1)

        # Previous position close to current
        prev_pos = (0.51, 0.49)
        result = _detect_tennis_ball_hsv(frame, prev_pos)

        self.assertIsNotNone(result)

    def test_detect_tracking_filter_far(self):
        """Test that distant detections are filtered when tracking."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        ball_x, ball_y, ball_r = 100, 400, 15  # Far from prev_pos
        cv2.circle(frame, (ball_x, ball_y), ball_r, (50, 200, 220), -1)

        # Previous position is far away
        prev_pos = (0.9, 0.1)
        result = _detect_tennis_ball_hsv(frame, prev_pos)

        # Should be filtered out due to distance
        self.assertIsNone(result)

    def test_detect_multiple_circles_prefers_best(self):
        """Test that when multiple circles exist, the best one is selected."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Add a tennis ball
        cv2.circle(frame, (320, 240), 20, (50, 200, 220), -1)
        # Add a small non-circular blob (should be filtered by circularity)
        # Using a thin rectangle so circularity is low
        cv2.fillPoly(frame, [np.array([[100, 100], [150, 100], [125, 130]])], (50, 200, 220))

        result = _detect_tennis_ball_hsv(frame, None)

        # The large circle should be detected
        self.assertIsNotNone(result)
        cx, cy, conf = result
        # Should detect the round ball
        self.assertGreater(conf, 0.3)

    def test_detect_small_ball_filtered(self):
        """Test that very small detections are filtered."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Add an extremely tiny tennis ball (below MIN_BALL_AREA_RATIO)
        # MIN_BALL_AREA_RATIO = 0.00005, so for 480x640: min_area = 480*640*0.00005 = 15.36
        # radius=1 means area ~3.14, which is below threshold
        cv2.circle(frame, (320, 240), 1, (50, 200, 220), -1)

        result = _detect_tennis_ball_hsv(frame, None)

        # Should be filtered out due to being too small
        self.assertIsNone(result)

    def test_detect_large_ball_filtered(self):
        """Test that very large detections are filtered."""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        # Add a very large "ball" (above MAX_BALL_AREA_RATIO)
        cv2.circle(frame, (320, 240), 100, (50, 200, 220), -1)

        result = _detect_tennis_ball_hsv(frame, None)

        self.assertIsNone(result)

    def test_hsv_thresholds_constants(self):
        """Verify HSV thresholds are within valid ranges."""
        self.assertGreaterEqual(HUE_MIN, 0)
        self.assertLessEqual(HUE_MAX, 180)
        self.assertGreaterEqual(SAT_MIN, 0)
        self.assertLessEqual(SAT_MAX, 255)
        self.assertGreaterEqual(VAL_MIN, 0)
        self.assertLessEqual(VAL_MAX, 255)
        self.assertLess(HUE_MIN, HUE_MAX)

    def test_ball_area_ratio_constants(self):
        """Verify ball area ratio constraints are valid."""
        self.assertGreater(MIN_BALL_AREA_RATIO, 0)
        self.assertGreater(MAX_BALL_AREA_RATIO, MIN_BALL_AREA_RATIO)
        self.assertLess(MAX_BALL_AREA_RATIO, 1.0)


class BallTrajectoryIntegrationTests(unittest.TestCase):
    """Integration tests for ball trajectory analysis."""

    @classmethod
    def setUpClass(cls):
        cls.video_path = Path("/workspace/tennis-highlight/data/uploads/input_video.mp4")
        if not cls.video_path.exists():
            raise unittest.SkipTest(f"Test video not found: {cls.video_path}")

        # Create temp dir for test artifacts
        cls.temp_dir = tempfile.mkdtemp()
        cls._original_analysis_dir = None

    @classmethod
    def tearDownClass(cls):
        # Cleanup temp artifacts
        if hasattr(cls, "temp_dir") and Path(cls.temp_dir).exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        # Override analysis dir for this test
        from app import config
        self._original_analysis_dir = config.settings.analysis_dir
        config.settings.analysis_dir = Path(self.temp_dir)
        config.settings.analysis_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        # Restore original analysis dir
        if self._original_analysis_dir:
            from app import config
            config.settings.analysis_dir = self._original_analysis_dir

        # Clean up test artifacts
        for f in Path(self.temp_dir).glob("test_*"):
            f.unlink(missing_ok=True)

    def test_analyze_ball_trajectory_full(self):
        """Test full ball trajectory analysis on sample video."""
        analysis_id = "test_full_001"

        artifact_path, artifact = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=5.0,
            range_start_s=0.0,
            range_end_s=10.0,
            progress_cb=None,
        )

        # Verify artifact structure
        self.assertIsInstance(artifact_path, Path)
        self.assertTrue(artifact_path.exists())
        self.assertIn("metadata", artifact)
        self.assertIn("summary", artifact)
        self.assertIn("trajectory", artifact)

        # Verify metadata
        meta = artifact["metadata"]
        self.assertEqual(meta["detector"], BALL_ALGORITHM)
        self.assertEqual(meta["detector_version"], BALL_ARTIFACT_VERSION)
        self.assertEqual(meta["sample_fps"], 5.0)
        self.assertEqual(meta["range_start_s"], 0.0)
        self.assertEqual(meta["range_end_s"], 10.0)
        self.assertGreater(meta["sample_count"], 0)

        # Verify summary
        summary = artifact["summary"]
        self.assertIn("total_samples", summary)
        self.assertIn("detected_frames", summary)
        self.assertIn("detection_rate", summary)
        self.assertIn("avg_confidence", summary)
        self.assertGreaterEqual(summary["total_samples"], 0)
        self.assertGreaterEqual(summary["detected_frames"], 0)
        self.assertLessEqual(summary["detection_rate"], 100.0)

        # Verify trajectory structure
        trajectory = artifact["trajectory"]
        self.assertIsInstance(trajectory, list)
        self.assertEqual(len(trajectory), summary["total_samples"])

        # Verify trajectory points
        for i, point in enumerate(trajectory[:5]):  # Check first 5
            self.assertIn("time_s", point)
            self.assertIn("confidence", point)
            self.assertGreaterEqual(point["time_s"], 0.0)
            self.assertLessEqual(point["time_s"], 10.0)

        # Detected frames should have x, y, bbox
        detected = [p for p in trajectory if p["x"] is not None]
        for point in detected[:5]:
            self.assertIsNotNone(point["x"])
            self.assertIsNotNone(point["y"])
            self.assertGreater(point["confidence"], 0.0)
            self.assertIsNotNone(point["bbox"])

        # Cleanup
        artifact_path.unlink(missing_ok=True)

        print(f"\nBall Detection Results:")
        print(f"  Total samples: {summary['total_samples']}")
        print(f"  Detected frames: {summary['detected_frames']}")
        print(f"  Detection rate: {summary['detection_rate']:.1f}%")
        print(f"  Avg confidence: {summary.get('avg_confidence', 0):.2f}")

    def test_analyze_ball_trajectory_short_range(self):
        """Test analysis with a short time range."""
        analysis_id = "test_short_001"

        artifact_path, artifact = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=10.0,
            range_start_s=2.0,
            range_end_s=4.0,
        )

        summary = artifact["summary"]
        # Should have ~20 samples (2 seconds * 10 fps)
        self.assertGreaterEqual(summary["total_samples"], 15)
        self.assertLessEqual(summary["total_samples"], 25)

        # Cleanup
        artifact_path.unlink(missing_ok=True)

    def test_analyze_ball_trajectory_different_fps(self):
        """Test analysis with different sample rates."""
        for fps in [2.0, 5.0, 10.0]:
            analysis_id = f"test_fps_{int(fps)}"

            artifact_path, artifact = analyze_ball_trajectory(
                analysis_id=analysis_id,
                video_path=self.video_path,
                video_duration_s=30.0,
                sample_fps=fps,
                range_start_s=0.0,
                range_end_s=5.0,
            )

            summary = artifact["summary"]
            # Allow some tolerance for fps variation
            expected_samples = 5.0 * fps
            self.assertAlmostEqual(
                summary["total_samples"],
                expected_samples,
                delta=expected_samples * 0.2,  # 20% tolerance
                msg=f"FPS {fps}: expected ~{expected_samples} samples",
            )

            # Cleanup
            artifact_path.unlink(missing_ok=True)

    def test_load_ball_artifact(self):
        """Test loading a saved ball artifact."""
        analysis_id = "test_load_001"

        # First create an artifact
        artifact_path, _ = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=5.0,
            range_start_s=0.0,
            range_end_s=5.0,
        )

        # Then load it
        artifact = load_ball_artifact(artifact_path)

        self.assertIn("trajectory", artifact)
        self.assertIn("metadata", artifact)
        self.assertIn("summary", artifact)
        self.assertIsInstance(artifact["trajectory"], list)
        self.assertGreater(len(artifact["trajectory"]), 0)

        # Cleanup
        artifact_path.unlink(missing_ok=True)

    def test_trajectory_temporal_ordering(self):
        """Verify trajectory points are in chronological order."""
        analysis_id = "test_order_001"

        artifact_path, artifact = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=5.0,
            range_start_s=0.0,
            range_end_s=5.0,
        )

        trajectory = artifact["trajectory"]
        times = [p["time_s"] for p in trajectory]

        # Verify times are monotonically increasing
        for i in range(1, len(times)):
            self.assertGreaterEqual(
                times[i], times[i - 1],
                f"Trajectory not in order at index {i}",
            )

        # Cleanup
        artifact_path.unlink(missing_ok=True)

    def test_trajectory_position_normalization(self):
        """Verify trajectory positions are normalized (0-1 range)."""
        analysis_id = "test_norm_001"

        artifact_path, artifact = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=5.0,
            range_start_s=0.0,
            range_end_s=5.0,
        )

        detected = [p for p in artifact["trajectory"] if p["x"] is not None]
        for point in detected:
            self.assertGreaterEqual(point["x"], 0.0)
            self.assertLessEqual(point["x"], 1.0)
            self.assertGreaterEqual(point["y"], 0.0)
            self.assertLessEqual(point["y"], 1.0)

        # Cleanup
        artifact_path.unlink(missing_ok=True)


class BallTrackingAPITests(unittest.TestCase):
    """API-level tests for ball tracking endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.video_path = Path("/workspace/tennis-highlight/data/uploads/input_video.mp4")
        if not cls.video_path.exists():
            raise unittest.SkipTest(f"Test video not found: {cls.video_path}")

        # Import FastAPI test client
        try:
            from fastapi.testclient import TestClient
            from app.main import app
            cls.client = TestClient(app)
        except ImportError as e:
            raise unittest.SkipTest(f"FastAPI TestClient not available: {e}")

    def test_algorithm_constant(self):
        """Verify BALL_ALGORITHM constant is valid."""
        self.assertEqual(BALL_ALGORITHM, "ball_tracking_color")
        self.assertIsInstance(BALL_ALGORITHM, str)

    def test_artifact_version_constant(self):
        """Verify BALL_ARTIFACT_VERSION is a positive integer."""
        self.assertIsInstance(BALL_ARTIFACT_VERSION, int)
        self.assertGreater(BALL_ARTIFACT_VERSION, 0)

    def test_ball_trajectory_from_video_endpoint(self):
        """Test the new /ball-trajectory-from-video/{video_id} endpoint."""
        # First get a video_id from the media endpoint
        response = self.client.get("/media")
        if response.status_code != 200:
            self.skipTest("No videos available in media pool")

        videos = response.json()
        if not videos:
            self.skipTest("No videos available in media pool")

        video_id = videos[0]["video_id"]

        # Test the new endpoint
        response = self.client.post(
            f"/ball-trajectory-from-video/{video_id}",
            json={
                "sample_fps": 4.0,
                "range_start_s": 0.0,
                "range_end_s": 5.0,
            },
        )

        self.assertIn(response.status_code, [200, 201, 202])
        data = response.json()
        self.assertIn("ball_analysis_id", data)
        self.assertEqual(data["status"], "started")

    def test_ball_trajectory_from_video_not_found(self):
        """Test /ball-trajectory-from-video with non-existent video."""
        response = self.client.post(
            "/ball-trajectory-from-video/nonexistent_video_id",
            json={"sample_fps": 4.0},
        )
        self.assertEqual(response.status_code, 404)

    def test_get_ball_trajectory_not_found(self):
        """Test GET /ball-trajectory/{analysis_id} returns empty for non-existent."""
        response = self.client.get("/ball-trajectory/nonexistent_analysis_id")
        self.assertEqual(response.status_code, 200)  # Returns empty data, not 404
        data = response.json()
        self.assertEqual(data["trajectory"], [])


class BallTrackingEdgeCaseTests(unittest.TestCase):
    """Edge case tests for ball tracking."""

    @classmethod
    def setUpClass(cls):
        cls.video_path = Path("/workspace/tennis-highlight/data/uploads/input_video.mp4")
        if not cls.video_path.exists():
            raise unittest.SkipTest(f"Test video not found: {cls.video_path}")

        cls.temp_dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temp_dir") and Path(cls.temp_dir).exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_zero_duration_range(self):
        """Test handling of zero-length analysis range."""
        from app import config

        original_dir = config.settings.analysis_dir
        config.settings.analysis_dir = Path(self.temp_dir)
        config.settings.analysis_dir.mkdir(parents=True, exist_ok=True)

        try:
            analysis_id = "test_zero_dur"
            artifact_path, artifact = analyze_ball_trajectory(
                analysis_id=analysis_id,
                video_path=self.video_path,
                video_duration_s=30.0,
                sample_fps=5.0,
                range_start_s=5.0,
                range_end_s=5.0,  # Same as start = 0 duration
            )

            summary = artifact["summary"]
            self.assertEqual(summary["total_samples"], 0)
            artifact_path.unlink(missing_ok=True)
        finally:
            config.settings.analysis_dir = original_dir

    def test_full_video_range(self):
        """Test analysis covering full video duration."""
        from app import config

        original_dir = config.settings.analysis_dir
        config.settings.analysis_dir = Path(self.temp_dir)
        config.settings.analysis_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Video is ~7 seconds, not 30
            analysis_id = "test_full_video"
            artifact_path, artifact = analyze_ball_trajectory(
                analysis_id=analysis_id,
                video_path=self.video_path,
                video_duration_s=7.0,
                sample_fps=2.0,
                range_start_s=0.0,
                range_end_s=7.0,
            )

            summary = artifact["summary"]
            # 7 seconds * 2 fps = 14 samples
            self.assertGreaterEqual(summary["total_samples"], 12)
            self.assertLessEqual(summary["total_samples"], 16)
            artifact_path.unlink(missing_ok=True)
        finally:
            config.settings.analysis_dir = original_dir


if __name__ == "__main__":
    # Run with verbose output
    unittest.main(verbosity=2)
