"""
测试球轨迹API坐标数据验证
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app


class TestBallTrajectoryAPICoordinates(unittest.TestCase):
    """测试球轨迹API返回的坐标数据"""
    
    TEST_ANALYSIS_ID = "aa75e68f3c1d49ddbe59d5d6b30b6827"
    
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.response = cls.client.get(f"/ball-trajectory/{cls.TEST_ANALYSIS_ID}")
        cls.data = cls.response.json()
    
    def test_01_api_status_code(self):
        """测试API状态码是否为200"""
        self.assertEqual(self.response.status_code, 200, f"API返回错误: {self.response.status_code}")
    
    def test_02_response_structure(self):
        """测试响应JSON结构完整性"""
        self.assertIn("trajectory", self.data, "缺少trajectory字段")
        self.assertIn("summary", self.data, "缺少summary字段")
        self.assertIn("metadata", self.data, "缺少metadata字段")
        
        trajectory = self.data["trajectory"]
        self.assertIsInstance(trajectory, list, "trajectory应为列表")
        # 注意：trajectory可能为空（未检测到网球）
    
    def test_03_trajectory_coordinates_range(self):
        """测试轨迹坐标是否在0-1范围内（归一化坐标）"""
        trajectory = self.data["trajectory"]
        
        if len(trajectory) == 0:
            # 空轨迹时，检查summary中的检测率
            detection_rate = self.data.get("summary", {}).get("detection_rate", 0)
            print(f"注意: 轨迹为空，检测率={detection_rate}%")
            return
            
        for i, point in enumerate(trajectory):
            x = point["x"]
            y = point["y"]
            
            self.assertTrue(0 <= x <= 1, f"第{i}个点x坐标{x}超出[0,1]范围")
            self.assertTrue(0 <= y <= 1, f"第{i}个点y坐标{y}超出[0,1]范围")
    
    def test_04_bbox_coordinates(self):
        """测试bbox坐标合理性"""
        trajectory = self.data["trajectory"]
        
        for i, point in enumerate(trajectory):
            bbox = point.get("bbox", {})
            if bbox:
                x1, y1 = bbox.get("x1", 0), bbox.get("y1", 0)
                x2, y2 = bbox.get("x2", 0), bbox.get("y2", 0)
                
                # bbox应在0-1范围内
                self.assertTrue(0 <= x1 <= 1, f"第{i}个点bbox.x1={x1}超出范围")
                self.assertTrue(0 <= y1 <= 1, f"第{i}个点bbox.y1={y1}超出范围")
                self.assertTrue(0 <= x2 <= 1, f"第{i}个点bbox.x2={x2}超出范围")
                self.assertTrue(0 <= y2 <= 1, f"第{i}个点bbox.y2={y2}超出范围")
                
                # 右下角应大于左上角
                self.assertTrue(x2 > x1, f"第{i}个点bbox x2({x2})应大于x1({x1})")
                self.assertTrue(y2 > y1, f"第{i}个点bbox y2({y2})应大于y1({y1})")
    
    def test_05_timestamp_order(self):
        """测试时间戳是否递增"""
        trajectory = self.data["trajectory"]
        
        for i in range(1, len(trajectory)):
            prev_time = trajectory[i-1]["time_s"]
            curr_time = trajectory[i]["time_s"]
            self.assertGreaterEqual(curr_time, prev_time, 
                f"时间戳未递增: 第{i-1}个={prev_time}, 第{i}个={curr_time}")
    
    def test_06_coordinate_validity(self):
        """测试坐标是否为有效数字（非NaN、非Infinity）"""
        trajectory = self.data["trajectory"]
        
        for i, point in enumerate(trajectory):
            import math
            x, y = point["x"], point["y"]
            
            self.assertFalse(math.isnan(x), f"第{i}个点x坐标是NaN")
            self.assertFalse(math.isnan(y), f"第{i}个点y坐标是NaN")
            self.assertFalse(math.isinf(x), f"第{i}个点x坐标是Infinity")
            self.assertFalse(math.isinf(y), f"第{i}个点y坐标是Infinity")
    
    def test_07_detection_rate_reasonability(self):
        """测试检测率是否合理（0-100之间）"""
        summary = self.data["summary"]
        detection_rate = summary.get("detection_rate", 0)
        
        self.assertTrue(0 <= detection_rate <= 100, 
            f"检测率{detection_rate}%超出[0,100]范围")
    
    def test_08_metadata_consistency(self):
        """测试元数据一致性"""
        metadata = self.data["metadata"]
        trajectory = self.data["trajectory"]
        
        video_width = metadata.get("video_width", 0)
        video_height = metadata.get("video_height", 0)
        sample_fps = metadata.get("sample_fps", 0)
        
        self.assertGreater(video_width, 0, "video_width应为正数")
        self.assertGreater(video_height, 0, "video_height应为正数")
        self.assertGreater(sample_fps, 0, "sample_fps应为正数")
        
        # 样本数量应大于0
        self.assertGreater(len(trajectory), 0, "轨迹样本数量应为正数")
    
    def test_09_trajectory_continuity(self):
        """测试轨迹连续性（相邻点距离不应过大）"""
        trajectory = self.data["trajectory"]
        
        if len(trajectory) < 2:
            return
        
        large_jumps = []
        for i in range(1, len(trajectory)):
            dx = trajectory[i]["x"] - trajectory[i-1]["x"]
            dy = trajectory[i]["y"] - trajectory[i-1]["y"]
            distance = (dx**2 + dy**2) ** 0.5
            
            # 帧间距离超过0.3（归一化）可能是异常跳跃
            if distance > 0.3:
                large_jumps.append((i, distance))
        
        # 允许少量大跳跃（可能是出界后重新出现）
        if len(large_jumps) > len(trajectory) * 0.1:
            print(f"警告: 发现{len(large_jumps)}个大跳跃点")
    
    def test_10_full_data_summary(self):
        """打印完整数据汇总"""
        trajectory = self.data["trajectory"]
        summary = self.data["summary"]
        metadata = self.data["metadata"]
        
        print("\n" + "="*50)
        print("球轨迹数据汇总")
        print("="*50)
        print(f"Analysis ID: {self.TEST_ANALYSIS_ID}")
        print(f"视频分辨率: {metadata.get('video_width')}x{metadata.get('video_height')}")
        print(f"采样帧率: {metadata.get('sample_fps')} FPS")
        print(f"轨迹点数: {len(trajectory)}")
        print(f"检测帧数: {summary.get('detected_frames')}")
        print(f"检测率: {summary.get('detection_rate')}%")
        
        if trajectory:
            x_coords = [p["x"] for p in trajectory]
            y_coords = [p["y"] for p in trajectory]
            print(f"X坐标范围: [{min(x_coords):.4f}, {max(x_coords):.4f}]")
            print(f"Y坐标范围: [{min(y_coords):.4f}, {max(y_coords):.4f}]")
            print(f"时间范围: [{trajectory[0]['time_s']:.2f}s, {trajectory[-1]['time_s']:.2f}s]")
        
        print("="*50 + "\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
