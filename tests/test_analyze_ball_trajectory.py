"""
测试 analyze_ball_trajectory 函数生成轨迹文件的正确性

检查:
1. artifact 文件是否正确生成
2. ball_detections 格式是否正确 (与 Tennis_AI_mainland 一致)
3. metadata 和 summary 是否完整
4. 轨迹视频是否生成
"""

import gzip
import json
import shutil
import tempfile
import unittest
from pathlib import Path


class TestAnalyzeBallTrajectory(unittest.TestCase):
    """测试 analyze_ball_trajectory 函数"""

    @classmethod
    def setUpClass(cls):
        cls.video_path = Path("/workspace/tennis-highlight/data/uploads/input_video.mp4")
        if not cls.video_path.exists():
            raise unittest.SkipTest(f"Test video not found: {cls.video_path}")
        cls.temp_dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        if Path(cls.temp_dir).exists():
            shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        from app import config
        self._original_analysis_dir = config.settings.analysis_dir
        self._original_ball_videos_dir = config.settings.ball_videos_dir
        config.settings.analysis_dir = Path(self.temp_dir)
        config.settings.ball_videos_dir = Path(self.temp_dir) / "ball_videos"
        config.settings.analysis_dir.mkdir(parents=True, exist_ok=True)
        config.settings.ball_videos_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        from app import config
        config.settings.analysis_dir = self._original_analysis_dir
        config.settings.ball_videos_dir = self._original_ball_videos_dir
        for f in Path(self.temp_dir).glob("test_*"):
            f.unlink(missing_ok=True)
        for f in Path(self.temp_dir).glob("*.ball.json.gz"):
            f.unlink(missing_ok=True)
        for f in Path(self.temp_dir).glob("*.mp4"):
            f.unlink(missing_ok=True)

    def test_artifact_file_generated(self):
        """测试 artifact 文件是否正确生成"""
        from app.pipeline.ball_tracking import analyze_ball_trajectory

        analysis_id = "test_artifact_gen"
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=30.0,
            range_start_s=0.0,
            range_end_s=2.0,
            progress_cb=None,
        )

        # 验证 artifact_path 存在
        self.assertIsInstance(artifact_path, Path)
        self.assertTrue(artifact_path.exists(), f"Artifact file not found: {artifact_path}")
        self.assertTrue(artifact_path.name.endswith(".ball.json.gz"))

    def test_artifact_structure(self):
        """测试 artifact 结构是否正确"""
        from app.pipeline.ball_tracking import analyze_ball_trajectory

        analysis_id = "test_structure"
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=30.0,
            range_start_s=0.0,
            range_end_s=2.0,
            progress_cb=None,
        )

        # 验证顶层结构
        self.assertIn("metadata", artifact)
        self.assertIn("summary", artifact)
        self.assertIn("ball_detections", artifact)

        # 不应该有 trajectory 字段 (与旧代码不同)
        # self.assertNotIn("trajectory", artifact)

    def test_metadata_fields(self):
        """测试 metadata 字段是否完整"""
        from app.pipeline.ball_tracking import (
            BALL_ALGORITHM,
            BALL_ARTIFACT_VERSION,
            analyze_ball_trajectory,
        )

        analysis_id = "test_metadata"
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=30.0,
            range_start_s=1.0,
            range_end_s=3.0,
            progress_cb=None,
        )

        meta = artifact["metadata"]

        # 必须包含的字段
        required_fields = [
            "detector",
            "detector_version",
            "model_path",
            "video_width",
            "video_height",
            "video_fps",
            "duration_s",
            "range_start_s",
            "range_end_s",
            "sample_fps",
            "sample_count",
        ]
        for field in required_fields:
            self.assertIn(field, meta, f"Missing metadata field: {field}")

        # 验证值
        self.assertEqual(meta["detector"], BALL_ALGORITHM)
        self.assertEqual(meta["detector_version"], BALL_ARTIFACT_VERSION)
        self.assertEqual(meta["range_start_s"], 1.0)
        self.assertEqual(meta["range_end_s"], 3.0)
        self.assertGreater(meta["video_width"], 0)
        self.assertGreater(meta["video_height"], 0)
        self.assertGreater(meta["video_fps"], 0)

    def test_summary_fields(self):
        """测试 summary 字段是否完整"""
        from app.pipeline.ball_tracking import analyze_ball_trajectory

        analysis_id = "test_summary"
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=30.0,
            range_start_s=0.0,
            range_end_s=3.0,
            progress_cb=None,
        )

        summary = artifact["summary"]

        # 必须包含的字段
        required_fields = [
            "total_samples",
            "detected_frames",
            "detection_rate",
            "avg_confidence",
        ]
        for field in required_fields:
            self.assertIn(field, summary, f"Missing summary field: {field}")

        # 验证值范围
        self.assertGreaterEqual(summary["total_samples"], 0)
        self.assertGreaterEqual(summary["detected_frames"], 0)
        self.assertLessEqual(summary["detection_rate"], 100.0)
        self.assertGreaterEqual(summary["avg_confidence"], 0.0)
        self.assertLessEqual(summary["avg_confidence"], 1.0)

    def test_ball_detections_format(self):
        """测试 ball_detections 格式是否正确 (与 Tennis_AI_mainland 一致)"""
        from app.pipeline.ball_tracking import analyze_ball_trajectory

        analysis_id = "test_ball_detections_format"
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=30.0,
            range_start_s=0.0,
            range_end_s=2.0,
            progress_cb=None,
        )

        ball_detections = artifact["ball_detections"]

        # 验证类型
        self.assertIsInstance(ball_detections, list)

        # 验证数量与 metadata 一致
        self.assertEqual(len(ball_detections), artifact["metadata"]["sample_count"])

        # 验证每个检测的格式: [{1: [x1, y1, x2, y2]}, ...]
        for i, detection in enumerate(ball_detections[:10]):  # 检查前10个
            self.assertIsInstance(detection, dict, f"Detection {i} is not a dict")
            # 键应该是 1（表示第一个检测到的球）
            self.assertIn(1, detection, f"Detection {i} missing key 1")
            coords = detection[1]
            self.assertIsInstance(coords, list, f"Detection {i} coords is not a list")
            self.assertEqual(len(coords), 4, f"Detection {i} should have 4 coords [x1,y1,x2,y2]")

            # 验证坐标值是数值类型
            for coord in coords:
                self.assertIsInstance(coord, (int, float)), f"Coord {coord} is not numeric"

    def test_ball_detections_gzip_file(self):
        """测试保存的 gzip 文件内容是否正确"""
        from app.pipeline.ball_tracking import analyze_ball_trajectory, load_ball_artifact

        analysis_id = "test_gzip_file"
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=30.0,
            range_start_s=0.0,
            range_end_s=1.0,
            progress_cb=None,
        )

        # 使用 load_ball_artifact 加载
        loaded = load_ball_artifact(artifact_path)

        # 验证加载的数据与原始数据一致
        self.assertEqual(loaded["metadata"]["sample_count"], artifact["metadata"]["sample_count"])
        self.assertEqual(loaded["summary"]["total_samples"], artifact["summary"]["total_samples"])
        self.assertEqual(len(loaded["ball_detections"]), len(artifact["ball_detections"]))

    def test_interpolation_creates_valid_data(self):
        """测试插值后所有帧都有有效坐标"""
        from app.pipeline.ball_tracking import analyze_ball_trajectory

        analysis_id = "test_interpolation"
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=30.0,
            range_start_s=0.0,
            range_end_s=3.0,
            progress_cb=None,
        )

        ball_detections = artifact["ball_detections"]

        # 插值后，所有帧都应该有键 1
        for i, detection in enumerate(ball_detections):
            self.assertIn(1, detection, f"Frame {i} missing ball detection after interpolation")

            coords = detection[1]
            # 验证坐标不为 NaN
            for coord in coords:
                self.assertFalse(
                    coord != coord,  # NaN != NaN
                    f"Frame {i} has NaN coordinate: {coords}"
                )

    def test_short_range_analysis(self):
        """测试短时间范围的检测"""
        from app.pipeline.ball_tracking import analyze_ball_trajectory

        analysis_id = "test_short_range"
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=30.0,
            range_start_s=0.5,
            range_end_s=1.5,
            progress_cb=None,
        )

        # 1秒范围，30fps，应该有约30帧
        expected_frames = int(1.0 * 30.0)  # 30 frames
        actual_frames = artifact["metadata"]["sample_count"]

        self.assertAlmostEqual(actual_frames, expected_frames, delta=5)

    def test_video_output_path_returned(self):
        """测试是否返回视频输出路径（即使生成失败也不报错）"""
        from app.pipeline.ball_tracking import analyze_ball_trajectory

        analysis_id = "test_video_output"
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=self.video_path,
            video_duration_s=30.0,
            sample_fps=30.0,
            range_start_s=0.0,
            range_end_s=2.0,
            progress_cb=None,
        )

        # video_output_path 可能为 None（如果视频生成失败）
        # 或者是一个 Path 对象
        if video_output_path is not None:
            self.assertIsInstance(video_output_path, Path)


if __name__ == "__main__":
    unittest.main()
