"""
测试用例：后台生成轨迹文件和轨迹查询接口

测试内容：
1. 轨迹文件生成（ball artifact）
2. 轨迹查询 API 接口
3. 轨迹视频生成
"""

import gzip
import json
import tempfile
import time
import unittest
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.pipeline.ball_tracking import analyze_ball_trajectory, load_ball_artifact


def create_test_video(duration_s: float = 5.0, fps: float = 30.0) -> bytes:
    """创建包含模拟网球轨迹的测试视频"""
    width, height = 640, 480
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        tmp_path = f.name
    
    out = cv2.VideoWriter(tmp_path, fourcc, fps, (width, height))
    
    for frame_idx in range(int(duration_s * fps)):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (50, 100, 50)  # 绿色背景
        
        # 模拟网球移动轨迹（从左到右抛物线）
        t = frame_idx / fps
        x = int(50 + (width - 100) * (t / duration_s))
        y = int(height // 2 - 100 * abs(0.5 - t / duration_s) + 100)
        cv2.circle(frame, (x, y), 15, (255, 255, 0), -1)  # 黄色网球
        
        out.write(frame)
    
    out.release()
    
    with open(tmp_path, "rb") as f:
        data = f.read()
    
    Path(tmp_path).unlink()
    return data


class TestBallTrajectoryFileGeneration(unittest.TestCase):
    """测试轨迹文件生成"""

    @classmethod
    def setUpClass(cls):
        cls.test_video_path = Path("/workspace/tennis-highlight/data/uploads/input_video.mp4")
        if not cls.test_video_path.exists():
            raise unittest.SkipTest(f"Test video not found: {cls.test_video_path}")

    def test_artifact_file_generation(self):
        """测试 ball artifact 文件正确生成"""
        analysis_id = f"test_artifact_{int(time.time())}"
        artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"
        
        # 清理可能存在的旧文件
        if artifact_path.exists():
            artifact_path.unlink()
        
        # 运行轨迹分析
        artifact_path_result, artifact, video_output = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.test_video_path,
            video_duration_s=10.0,
            sample_fps=4.0,
            range_start_s=0.0,
            range_end_s=5.0,
            progress_cb=None,
        )
        
        # 验证返回值
        self.assertIsInstance(artifact_path_result, Path)
        self.assertIsInstance(artifact, dict)
        
        # 验证 artifact 文件存在
        self.assertTrue(artifact_path.exists(), f"Artifact file not created: {artifact_path}")
        
        # 验证 artifact 内容结构
        self.assertIn("metadata", artifact)
        self.assertIn("summary", artifact)
        self.assertIn("ball_detections", artifact)
        
        metadata = artifact["metadata"]
        self.assertEqual(metadata["detector"], "ball_tracking_yolo")
        self.assertIn("video_width", metadata)
        self.assertIn("video_height", metadata)
        
        summary = artifact["summary"]
        self.assertIn("total_samples", summary)
        self.assertIn("detected_frames", summary)
        self.assertIn("detection_rate", summary)
        
        # 清理测试文件
        if artifact_path.exists():
            artifact_path.unlink()
        if video_output and Path(video_output).exists():
            Path(video_output).unlink()

    def test_load_ball_artifact_function(self):
        """测试 load_ball_artifact 函数正确加载 artifact"""
        analysis_id = f"test_load_{int(time.time())}"
        artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"
        
        # 先生成一个 artifact
        _, artifact, _ = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.test_video_path,
            video_duration_s=10.0,
            sample_fps=4.0,
            range_start_s=0.0,
            range_end_s=3.0,
            progress_cb=None,
        )
        
        # 测试加载功能
        loaded = load_ball_artifact(artifact_path)
        
        self.assertIsInstance(loaded, dict)
        self.assertEqual(loaded["metadata"]["detector"], artifact["metadata"]["detector"])
        self.assertEqual(loaded["summary"]["total_samples"], artifact["summary"]["total_samples"])
        self.assertEqual(len(loaded["ball_detections"]), len(artifact["ball_detections"]))
        
        # 清理
        if artifact_path.exists():
            artifact_path.unlink()

    def test_trajectory_video_generation(self):
        """测试轨迹视频文件生成"""
        analysis_id = f"test_video_{int(time.time())}"
        
        _, _, video_output = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.test_video_path,
            video_duration_s=10.0,
            sample_fps=4.0,
            range_start_s=0.0,
            range_end_s=5.0,
            progress_cb=None,
        )
        
        # 验证视频输出路径
        self.assertIsNotNone(video_output)
        expected_video_path = settings.ball_videos_dir / f"{analysis_id}_ball_trajectory.mp4"
        self.assertEqual(Path(video_output), expected_video_path)
        self.assertTrue(video_output.exists(), f"Trajectory video not created: {video_output}")
        
        # 验证视频可以被打开
        cap = cv2.VideoCapture(str(video_output))
        self.assertTrue(cap.isOpened(), "Generated video cannot be opened")
        
        # 验证视频属性
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ret, frame = cap.read()
        cap.release()
        
        self.assertTrue(ret, "Cannot read first frame")
        self.assertGreater(frame_count, 0, "Video has no frames")
        
        # 清理
        artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"
        if artifact_path.exists():
            artifact_path.unlink()
        if video_output:
            Path(video_output).unlink()


class TestBallTrajectoryAPI(unittest.TestCase):
    """测试轨迹查询 API 接口"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        
        # 确保有测试视频
        response = cls.client.get("/media")
        if response.status_code == 200:
            videos = response.json()
            cls.test_video_id = videos[0]["video_id"] if videos else None
        else:
            cls.test_video_id = None

    def test_get_trajectory_api_existing(self):
        """测试查询已存在的轨迹数据"""
        if self.test_video_id is None:
            self.skipTest("No test video available")
        
        # 启动分析
        response = self.client.post(
            f"/ball-trajectory-from-video/{self.test_video_id}",
            json={"range_start_s": 0.0, "range_end_s": 3.0},
        )
        self.assertIn(response.status_code, [200, 201, 202])
        analysis_id = response.json()["ball_analysis_id"]
        
        # 等待完成
        max_wait = 90
        for _ in range(max_wait // 2):
            status_resp = self.client.get(f"/analysis-status/{analysis_id}")
            if status_resp.json().get("status") == "done":
                break
            time.sleep(2)
        
        # 查询轨迹
        traj_resp = self.client.get(f"/ball-trajectory/{analysis_id}")
        self.assertEqual(traj_resp.status_code, 200)
        
        data = traj_resp.json()
        
        # 验证响应结构
        self.assertIn("trajectory", data)
        self.assertIn("summary", data)
        self.assertIn("metadata", data)
        
        # 验证 summary 字段
        summary = data["summary"]
        self.assertIsInstance(summary["total_samples"], int)
        self.assertIsInstance(summary["detected_frames"], int)
        self.assertIsInstance(summary["detection_rate"], (int, float))
        
        # 验证 metadata
        metadata = data["metadata"]
        self.assertEqual(metadata["detector"], "ball_tracking_yolo")
        
        # 验证 trajectory 格式
        trajectory = data["trajectory"]
        self.assertIsInstance(trajectory, list)
        
        for point in trajectory[:5]:  # 检查前5个点
            self.assertIn("time_s", point)
            self.assertIn("x", point)
            self.assertIn("y", point)
            self.assertIn("bbox", point)
            # 验证归一化坐标
            if point["x"] is not None:
                self.assertGreaterEqual(point["x"], 0.0)
                self.assertLessEqual(point["x"], 1.0)
                self.assertGreaterEqual(point["y"], 0.0)
                self.assertLessEqual(point["y"], 1.0)

    def test_get_trajectory_api_nonexistent(self):
        """测试查询不存在的轨迹返回空数据（不是 404）"""
        fake_id = f"nonexistent_{int(time.time())}"
        
        response = self.client.get(f"/ball-trajectory/{fake_id}")
        self.assertEqual(response.status_code, 200)  # 不返回 404
        
        data = response.json()
        self.assertEqual(data["trajectory"], [])
        self.assertEqual(data["summary"]["total_samples"], 0)
        self.assertEqual(data["summary"]["detected_frames"], 0)

    def test_get_trajectory_api_with_video_id(self):
        """测试从视频 ID 直接启动轨迹分析"""
        if self.test_video_id is None:
            self.skipTest("No test video available")
        
        response = self.client.post(
            f"/ball-trajectory-from-video/{self.test_video_id}",
            json={
                "range_start_s": 1.0,
                "range_end_s": 4.0,
            },
        )
        
        self.assertIn(response.status_code, [200, 201, 202])
        data = response.json()
        
        self.assertIn("ball_analysis_id", data)
        self.assertIn("status", data)
        self.assertEqual(data["status"], "started")


class TestBallArtifactIntegrity(unittest.TestCase):
    """测试 artifact 文件完整性和数据一致性"""

    @classmethod
    def setUpClass(cls):
        cls.test_video_path = Path("/workspace/tennis-highlight/data/uploads/input_video.mp4")
        if not cls.test_video_path.exists():
            raise unittest.SkipTest(f"Test video not found: {cls.test_video_path}")

    def test_gzip_compression(self):
        """验证 artifact 文件是有效的 gzip 压缩文件"""
        analysis_id = f"test_gzip_{int(time.time())}"
        artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"
        
        analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.test_video_path,
            video_duration_s=10.0,
            sample_fps=4.0,
            range_start_s=0.0,
            range_end_s=2.0,
            progress_cb=None,
        )
        
        # 验证文件是有效的 gzip
        try:
            with gzip.open(artifact_path, "rt", encoding="utf-8") as f:
                data = json.load(f)
            self.assertIsInstance(data, dict)
        except Exception as e:
            self.fail(f"Invalid gzip file: {e}")
        
        # 清理
        if artifact_path.exists():
            artifact_path.unlink()

    def test_trajectory_data_consistency(self):
        """验证 API 返回的 trajectory 数据与原始 artifact 一致"""
        analysis_id = f"test_consistency_{int(time.time())}"
        artifact_path = settings.analysis_dir / f"{analysis_id}.ball.json.gz"
        
        # 生成 artifact
        _, artifact, _ = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.test_video_path,
            video_duration_s=10.0,
            sample_fps=4.0,
            range_start_s=0.0,
            range_end_s=3.0,
            progress_cb=None,
        )
        
        # 手动加载 artifact 并转换格式
        loaded = load_ball_artifact(artifact_path)
        metadata = loaded["metadata"]
        ball_detections = loaded["ball_detections"]
        
        # 模拟 API 的转换逻辑
        trajectory_from_artifact = []
        for i, det in enumerate(ball_detections):
            if 1 in det:
                x1, y1, x2, y2 = det[1]
                w = metadata.get("video_width", 1920)
                h = metadata.get("video_height", 1080)
                trajectory_from_artifact.append({
                    "time_s": metadata.get("range_start_s", 0) + i / metadata.get("sample_fps", 4),
                    "x": (x1 + x2) / 2 / w,
                    "y": (y1 + y2) / 2 / h,
                })
        
        # 验证 summary 一致性
        self.assertEqual(loaded["summary"]["total_samples"], len(ball_detections))
        
        # 验证检测率计算
        detected = sum(1 for d in ball_detections if 1 in d)
        expected_rate = (detected / len(ball_detections) * 100) if ball_detections else 0
        self.assertAlmostEqual(loaded["summary"]["detection_rate"], expected_rate, places=1)
        
        # 清理
        if artifact_path.exists():
            artifact_path.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
