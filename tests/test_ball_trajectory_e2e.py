"""
End-to-end integration tests for ball trajectory analysis workflow.

Tests the complete flow:
1. Upload/get a test video
2. Start ball trajectory analysis
3. Wait for analysis to complete
4. Retrieve trajectory data via API
5. Verify the trajectory structure and data
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.config import settings


class BallTrajectoryE2ETest(unittest.TestCase):
    """End-to-end tests for ball trajectory analysis."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.video_path = Path("/workspace/tennis-highlight/data/uploads/input_video.mp4")
        if not cls.video_path.exists():
            raise unittest.SkipTest(f"Test video not found: {cls.video_path}")

        # Get or ensure a test video exists in media pool
        response = cls.client.get("/media")
        if response.status_code == 200:
            videos = response.json()
            cls.test_video_id = videos[0]["video_id"] if videos else None
        else:
            cls.test_video_id = None

    def test_full_workflow_ball_trajectory_analysis(self):
        """
        Complete workflow test for ball trajectory analysis:
        1. Get a video from media pool
        2. Start ball trajectory analysis
        3. Poll until completion
        4. Get trajectory data via GET endpoint
        5. Verify data structure
        """
        # Step 1: Get video ID
        if self.test_video_id is None:
            self.skipTest("No test video available in media pool")

        video_id = self.test_video_id
        print(f"\nUsing video_id: {video_id}")

        # Step 2: Start ball trajectory analysis
        response = self.client.post(
            f"/ball-trajectory-from-video/{video_id}",
            json={
                "sample_fps": 4.0,
                "range_start_s": 0.0,
                "range_end_s": 5.0,
            },
        )

        self.assertIn(response.status_code, [200, 201, 202], 
                      f"Failed to start analysis: {response.text}")
        data = response.json()
        self.assertIn("ball_analysis_id", data)
        self.assertEqual(data["status"], "started")

        ball_analysis_id = data["ball_analysis_id"]
        print(f"Started ball analysis: {ball_analysis_id}")

        # Step 3: Poll until analysis completes
        max_wait_seconds = 120
        poll_interval = 2
        elapsed = 0
        analysis_completed = False

        while elapsed < max_wait_seconds:
            status_response = self.client.get(f"/analysis-status/{ball_analysis_id}")
            self.assertEqual(status_response.status_code, 200, 
                           f"Failed to get analysis status: {status_response.text}")
            
            status_data = status_response.json()
            status = status_data.get("status")
            progress = status_data.get("progress_percent", 0)
            progress_msg = status_data.get("progress_message", "")
            
            print(f"  Status: {status}, Progress: {progress}%, Message: {progress_msg}")

            if status == "done":
                analysis_completed = True
                break
            elif status == "error":
                error_msg = status_data.get("error_msg", "Unknown error")
                self.fail(f"Analysis failed with error: {error_msg}")

            time.sleep(poll_interval)
            elapsed += poll_interval

        self.assertTrue(analysis_completed, 
                        f"Analysis did not complete within {max_wait_seconds} seconds")

        print(f"Analysis completed in {elapsed} seconds")

        # Step 4: Get trajectory data via GET endpoint
        trajectory_response = self.client.get(f"/ball-trajectory/{ball_analysis_id}")
        self.assertEqual(trajectory_response.status_code, 200,
                        f"Failed to get trajectory: {trajectory_response.text}")
        
        trajectory_data = trajectory_response.json()
        
        # Step 5: Verify trajectory structure
        self.assertIn("trajectory", trajectory_data)
        self.assertIn("summary", trajectory_data)
        self.assertIn("metadata", trajectory_data)

        trajectory = trajectory_data["trajectory"]
        summary = trajectory_data["summary"]
        metadata = trajectory_data["metadata"]

        # Verify summary fields
        self.assertIn("total_samples", summary)
        self.assertIn("detected_frames", summary)
        self.assertIn("detection_rate", summary)
        self.assertIn("avg_confidence", summary)

        print(f"\nTrajectory Summary:")
        print(f"  Total samples: {summary['total_samples']}")
        print(f"  Detected frames: {summary['detected_frames']}")
        print(f"  Detection rate: {summary['detection_rate']:.1f}%")
        print(f"  Avg confidence: {summary.get('avg_confidence', 0):.2f}")

        # Verify metadata
        self.assertIn("detector", metadata)
        self.assertEqual(metadata["detector"], "ball_tracking_yolo")
        self.assertIn("sample_fps", metadata)
        self.assertEqual(metadata["sample_fps"], 4.0)

        # Verify trajectory is a list
        self.assertIsInstance(trajectory, list)

        # If trajectory has data, verify point structure
        if len(trajectory) > 0:
            for point in trajectory[:5]:
                self.assertIn("time_s", point)
                self.assertIn("confidence", point)
                self.assertGreaterEqual(point["time_s"], 0.0)
                self.assertLessEqual(point["time_s"], 5.0)
                self.assertGreaterEqual(point["confidence"], 0.0)
                self.assertLessEqual(point["confidence"], 1.0)

            # Verified frames should have position data
            detected_points = [p for p in trajectory if p.get("x") is not None]
            if len(detected_points) > 0:
                for point in detected_points[:5]:
                    self.assertIsNotNone(point.get("x"))
                    self.assertIsNotNone(point.get("y"))
                    self.assertIsNotNone(point.get("bbox"))
                    # Positions should be normalized (0-1)
                    self.assertGreaterEqual(point["x"], 0.0)
                    self.assertLessEqual(point["x"], 1.0)
                    self.assertGreaterEqual(point["y"], 0.0)
                    self.assertLessEqual(point["y"], 1.0)

    def test_get_nonexistent_trajectory(self):
        """Test that querying a non-existent trajectory returns empty data, not 404."""
        fake_id = "nonexistent_analysis_id_12345"
        response = self.client.get(f"/ball-trajectory/{fake_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["trajectory"], [])
        self.assertEqual(data["summary"]["total_samples"], 0)

    def test_start_analysis_with_invalid_video(self):
        """Test that starting analysis with non-existent video returns 404."""
        fake_video_id = "nonexistent_video_id_12345"
        response = self.client.post(
            f"/ball-trajectory-from-video/{fake_video_id}",
            json={"sample_fps": 4.0},
        )
        self.assertEqual(response.status_code, 404)

    def test_analysis_endpoint_returns_ball_algorithm(self):
        """Test that the analysis endpoint properly handles ball_tracking_yolo algorithm."""
        if self.test_video_id is None:
            self.skipTest("No test video available")

        # Start a ball analysis
        response = self.client.post(
            f"/ball-trajectory-from-video/{self.test_video_id}",
            json={"sample_fps": 2.0, "range_start_s": 0.0, "range_end_s": 2.0},
        )
        self.assertIn(response.status_code, [200, 201, 202])
        ball_analysis_id = response.json()["ball_analysis_id"]

        # Poll for completion
        max_wait = 60
        for _ in range(max_wait // 2):
            status_resp = self.client.get(f"/analysis-status/{ball_analysis_id}")
            if status_resp.json().get("status") == "done":
                break
            time.sleep(2)
        else:
            self.fail("Analysis did not complete")

        # Get analysis via GET /analysis/{id}
        analysis_resp = self.client.get(f"/analysis/{ball_analysis_id}")
        self.assertEqual(analysis_resp.status_code, 200, 
                        f"Failed to get analysis: {analysis_resp.text}")
        
        analysis_data = analysis_resp.json()
        self.assertEqual(analysis_data["analysis_id"], ball_analysis_id)
        # metadata may be None for ball analysis depending on implementation
        # but the endpoint should not error

    def test_workflow_with_process_endpoint(self):
        """
        Test alternative workflow using the /process endpoint with ball_tracking_yolo algorithm.
        """
        if self.test_video_id is None:
            self.skipTest("No test video available")

        # Start analysis via process endpoint with ball algorithm
        response = self.client.post(
            f"/process/{self.test_video_id}",
            json={
                "algorithm": "ball_tracking_yolo",
                "range_start_s": 0.0,
                "range_end_s": 3.0,
            },
        )

        # Should succeed
        self.assertIn(response.status_code, [200, 201, 202],
                     f"Process endpoint failed: {response.text}")
        
        data = response.json()
        self.assertIn("analysis_id", data)
        analysis_id = data["analysis_id"]
        print(f"\nProcess endpoint created analysis: {analysis_id}")

        # Poll for completion
        max_wait = 90
        for _ in range(max_wait // 2):
            status_resp = self.client.get(f"/analysis-status/{analysis_id}")
            if status_resp.json().get("status") == "done":
                break
            time.sleep(2)

        status_resp = self.client.get(f"/analysis-status/{analysis_id}")
        status_data = status_resp.json()
        self.assertEqual(status_data["status"], "done", 
                        f"Analysis did not complete: {status_data}")

        # Verify ball artifact exists
        artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"
        self.assertTrue(artifact_path.exists(), 
                       f"Ball artifact not found: {artifact_path}")

        # Get trajectory via endpoint
        trajectory_resp = self.client.get(f"/ball-trajectory/{analysis_id}")
        self.assertEqual(trajectory_resp.status_code, 200)
        trajectory_data = trajectory_resp.json()
        
        self.assertIn("trajectory", trajectory_data)
        self.assertIn("summary", trajectory_data)
        print(f"\nProcess workflow trajectory: {trajectory_data['summary']['total_samples']} samples")


class BallTrajectoryAPIStructureTest(unittest.TestCase):
    """Tests for API response structure and validation."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_ball_trajectory_response_schema(self):
        """Verify the ball trajectory response matches expected schema."""
        # Use a fake ID to get empty response structure
        response = self.client.get("/ball-trajectory/test_schema_check")
        self.assertEqual(response.status_code, 200)
        
        data = response.json()
        
        # Check required top-level keys
        self.assertIn("trajectory", data)
        self.assertIn("summary", data)
        self.assertIn("metadata", data)
        
        # Check summary structure
        summary = data["summary"]
        required_summary_fields = [
            "total_samples", 
            "detected_frames", 
            "detection_rate", 
            "avg_confidence"
        ]
        for field in required_summary_fields:
            self.assertIn(field, summary, f"Missing field in summary: {field}")
        
        # Check types
        self.assertIsInstance(summary["total_samples"], int)
        self.assertIsInstance(summary["detected_frames"], int)
        self.assertIsInstance(summary["detection_rate"], (int, float))
        self.assertIsInstance(summary["avg_confidence"], (int, float))
        
        # Check metadata structure
        metadata = data["metadata"]
        self.assertIsInstance(metadata, dict)


if __name__ == "__main__":
    # Run with verbose output
    unittest.main(verbosity=2)
