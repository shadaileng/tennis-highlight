"""
测试整合后的击球检测功能
比较基于球拍检测和基于轨迹分析两种方法
"""

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent / "app"))

from pipeline.ball_tracking import get_ball_shot_frames, _convert_to_hit_moments
from pipeline.racket_tracking import detect_racket, find_hit_moments
import cv2
import numpy as np
import pandas as pd


def create_test_ball_detections(num_frames=300):
    """创建测试用的球检测数据 (模拟击球轨迹)"""
    ball_detections = []
    
    # 模拟球从下往上飞 (y 减小)
    for i in range(100):
        y_pos = 500 - i * 2  # 球向上移动
        ball_detections.append({1: [300, y_pos, 320, y_pos + 20]})
    
    # 模拟击球后球改变方向 (y 增大)
    for i in range(100):
        y_pos = 300 + i * 2  # 球向下移动
        ball_detections.append({1: [400, y_pos, 420, y_pos + 20]})
    
    # 再次击球，球改变方向
    for i in range(100):
        y_pos = 500 - i * 2  # 球向上移动
        ball_detections.append({1: [500, y_pos, 520, y_pos + 20]})
    
    return ball_detections


def test_trajectory_based_detection():
    """测试基于轨迹的击球检测"""
    print("=" * 60)
    print("测试1: 基于轨迹的击球检测 (从 Tennis_AI_mainland 移植)")
    print("=" * 60)
    
    # 创建测试数据
    ball_detections = create_test_ball_detections()
    print(f"创建了 {len(ball_detections)} 帧的测试数据")
    
    # 检测击球时刻
    shot_frames = get_ball_shot_frames(ball_detections)
    
    print(f"检测到 {len(shot_frames)} 个击球时刻:")
    for frame_idx in shot_frames:
        print(f"  - 帧 {frame_idx}")
    
    # 转换为 hit_moments 格式
    hit_moments = _convert_to_hit_moments(ball_detections, shot_frames, video_fps=30.0)
    
    print(f"\n转换为 hit_moments 格式:")
    for moment in hit_moments:
        print(f"  - 帧 {moment['frame_idx']}, 时间 {moment['time_s']:.2f}s, "
              f"球位置 {moment['ball_pos']}, 方法 {moment['detection_method']}")
    
    return hit_moments


def test_combined_detection():
    """测试整合两种检测方法"""
    print("\n" + "=" * 60)
    print("测试2: 整合两种击球检测方法")
    print("=" * 60)
    
    # 创建测试数据
    ball_detections = create_test_ball_detections()
    
    # 模拟球拍检测 (在某些帧检测到球拍)
    racket_detections = []
    for i in range(len(ball_detections)):
        # 在击球时刻附近模拟球拍检测
        if 95 <= i <= 105 or 195 <= i <= 205:
            racket_detections.append({
                "x1": 350, "y1": 250, "x2": 450, "y2": 350,
                "cx": 400, "cy": 300, "conf": 0.8
            })
        else:
            racket_detections.append(None)
    
    # 方法1: 基于球拍检测
    print("\n方法1: 基于球拍检测")
    racket_hit_moments = find_hit_moments(ball_detections, racket_detections, video_fps=30.0)
    print(f"  检测到 {len(racket_hit_moments)} 个击球时刻:")
    for moment in racket_hit_moments:
        print(f"    - 帧 {moment['frame_idx']}, 时间 {moment['time_s']:.2f}s")
    
    # 方法2: 基于轨迹分析
    print("\n方法2: 基于轨迹分析")
    shot_frames = get_ball_shot_frames(ball_detections)
    trajectory_hit_moments = _convert_to_hit_moments(ball_detections, shot_frames, video_fps=30.0)
    print(f"  检测到 {len(trajectory_hit_moments)} 个击球时刻:")
    for moment in trajectory_hit_moments:
        print(f"    - 帧 {moment['frame_idx']}, 时间 {moment['time_s']:.2f}s")
    
    # 合并结果 (去重)
    print("\n合并结果 (去重):")
    all_hit_moments = racket_hit_moments + trajectory_hit_moments
    
    seen_frames = set()
    unique_hit_moments = []
    for moment in all_hit_moments:
        frame_idx = moment['frame_idx']
        if frame_idx not in seen_frames:
            seen_frames.add(frame_idx)
            unique_hit_moments.append(moment)
    
    print(f"  总共 {len(unique_hit_moments)} 个唯一击球时刻:")
    for moment in unique_hit_moments:
        method = moment.get('detection_method', 'racket_detection')
        print(f"    - 帧 {moment['frame_idx']}, 时间 {moment['time_s']:.2f}s, 方法: {method}")
    
    return unique_hit_moments


def test_with_real_video(video_path):
    """使用真实视频测试击球检测"""
    print("\n" + "=" * 60)
    print(f"测试3: 使用真实视频测试 - {video_path}")
    print("=" * 60)
    
    if not Path(video_path).exists():
        print(f"视频文件不存在: {video_path}")
        return
    
    # 这里需要实际的视频文件路径
    # 由于我们没有真实视频，这里只是示例框架
    print("注意: 需要真实视频文件才能运行此测试")
    print("请确保视频文件路径正确，并且包含网球和球拍")


if __name__ == "__main__":
    print("\n🎾 测试整合后的击球检测功能\n")
    
    # 测试1: 基于轨迹的击球检测
    test_trajectory_based_detection()
    
    # 测试2: 整合两种检测方法
    test_combined_detection()
    
    # 测试3: 使用真实视频 (可选)
    # test_with_real_video("path/to/your/video.mp4")
    
    print("\n✅ 测试完成!")
