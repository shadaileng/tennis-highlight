"""
快速测试：运行球追踪 + 骨骼检测，渲染带骨骼的视频
用法: python tests/test_pose_render.py [--duration N] [--max-frames N]
"""
import argparse
import gzip
import json
import time
from pathlib import Path

import cv2

# 确保 project root 在 PYTHONPATH 中
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.pipeline.ball_tracking import (
    analyze_ball_trajectory,
    _get_ball_model,
    _extract_pose_detections,
    DETECTION_STEP,
)
from app.config import settings


def main():
    parser = argparse.ArgumentParser(description="测试骨骼检测与渲染")
    parser.add_argument("video", nargs="?", type=str,
                        default=str(Path(__file__).parent.parent / "data" / "uploads" / "input_video.mp4"),
                        help="输入视频路径")
    parser.add_argument("--duration", type=float, default=5.0, help="测试时长（秒）")
    parser.add_argument("--max-frames", type=int, default=None, help="最大帧数（覆盖 duration）")
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.exists():
        print(f"错误: 视频文件不存在: {video_path}")
        return 1

    import cv2
    cap = cv2.VideoCapture(str(video_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_s = total_frames / video_fps
    cap.release()
    print(f"视频: {video_path}  {total_frames}帧, {video_fps:.2f}fps, {duration_s:.1f}s")

    if args.max_frames:
        range_end = args.max_frames / video_fps
    else:
        range_end = min(args.duration, duration_s)

    analysis_id = f"test_pose_{int(time.time())}"

    print(f"\n运行分析 (时长={range_end:.1f}s)...")
    artifact_path, artifact, video_path_out = analyze_ball_trajectory(
        analysis_id=analysis_id,
        video_path=video_path,
        video_duration_s=duration_s,
        sample_fps=video_fps,
        range_start_s=0.0,
        range_end_s=range_end,
        progress_cb=lambda pct, msg, eta: print(f"  [{pct:.0f}%] {msg}"),
    )

    print(f"\nArtifact: {artifact_path}")
    print(f"渲染视频: {video_path_out}")

    # 检查骨骼数据
    player_kps = artifact.get("player_keypoints")
    if player_kps:
        near_id = player_kps.get("near_player_id")
        fkps = player_kps.get("frame_keypoints", [])
        valid_count = sum(1 for f in fkps if f is not None)
        print(f"\n骨骼数据:")
        print(f"  近端球员 ID: {near_id}")
        print(f"  总帧数: {len(fkps)}")
        print(f"  有骨骼帧数: {valid_count}")
        if valid_count > 0:
            first_valid = next(f for f in fkps if f is not None)
            kps = first_valid.get("keypoints", [])
            print(f"  关键点数量: {len(kps)}")
            if kps:
                print(f"  样例 (nose): x={kps[0][0]:.1f} y={kps[0][1]:.1f} conf={kps[0][2]:.2f}")
    else:
        print("\n⚠️ 没有骨骼数据")

    print(f"\n完成！输出视频: {video_path_out}")
    return 0


if __name__ == "__main__":
    exit(main())
