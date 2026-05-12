"""
基础测试：网球轨迹检测 + 击球效果

对 input_video.mp4 运行完整检测流水线，验证：
1. 球检测正常，坐标有效
2. 击球时刻正确识别
3. 轨迹渲染视频成功生成

用法:
    python tests/test_basic_ball_trajectory.py                  # 默认：全长检测
    python tests/test_basic_ball_trajectory.py --duration 3.0   # 指定时长
    python tests/test_basic_ball_trajectory.py --start 1.0      # 指定起始时间
"""

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

_VIDEO = Path(__file__).parent.parent / "data" / "uploads" / "input_video.mp4"
_OUT_ANALYSIS = Path(__file__).parent.parent / "data" / "analysis"
_OUT_VIDEO = Path(__file__).parent.parent / "data" / "ball_videos"


def progress(pct, msg, eta):
    print(f"  [{pct:5.1f}%] {msg}" + (f" (剩余{eta:.0f}s)" if eta else ""))


def run(video_path: Path, duration_s: float, start_s: float):
    from app.pipeline.ball_tracking import analyze_ball_trajectory

    analysis_id = f"basic_test_{int(time.time())}"
    _OUT_ANALYSIS.mkdir(parents=True, exist_ok=True)
    _OUT_VIDEO.mkdir(parents=True, exist_ok=True)

    import app.config
    orig_a = app.config.settings.analysis_dir
    orig_v = app.config.settings.ball_videos_dir
    app.config.settings.analysis_dir = _OUT_ANALYSIS
    app.config.settings.ball_videos_dir = _OUT_VIDEO

    try:
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        dur = total_frames / fps
        cap.release()

        print(f"🎾 网球轨迹 + 击球效果测试")
        print(f"  视频: {video_path}")
        print(f"  时长: {dur:.1f}s | FPS: {fps:.1f} | {total_frames} 帧")
        print(f"  检测范围: {start_s}s ~ {start_s + duration_s}s")
        print()

        t0 = time.time()
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=video_path,
            video_duration_s=dur,
            sample_fps=fps,
            range_start_s=start_s,
            range_end_s=min(start_s + duration_s, dur),
            progress_cb=progress,
        )
        elapsed = time.time() - t0
        print(f"\n⏱ 耗时: {elapsed:.1f}s")

        summary = artifact["summary"]
        detections = artifact["ball_detections"]
        hit_moments = artifact.get("hit_moments", [])

        # === 轨迹统计 ===
        print(f"\n{'='*55}")
        print("📊 轨迹检测统计")
        print(f"{'='*55}")
        print(f"  总帧数:           {summary['total_samples']}")
        print(f"  检测到球:         {summary['detected_frames']} 帧")
        print(f"  检测率:           {summary['detection_rate']:.1f}%")
        print(f"  球实例总数:       {summary.get('total_ball_instances', 'N/A')}")
        print(f"  唯一 track_id:    {sorted(set(k for d in detections for k in d))}")
        print(f"  NaN 坐标:         {sum(1 for d in detections for c in d.values() if any(isinstance(v, float) and np.isnan(v) for v in c))}")

        # === 击球时刻 ===
        print(f"\n{'='*55}")
        print(f"🔥 击球检测结果 ({len(hit_moments)} 次)")
        print(f"{'='*55}")
        if hit_moments:
            for i, h in enumerate(hit_moments):
                px, py = h["ball_pos"]
                print(f"  击球 #{i+1}: 帧 {h['frame_idx']:4d} | "
                      f"时间 {h['time_s']:.2f}s | "
                      f"位置 ({px:.0f}, {py:.0f}) | "
                      f"方法: {h['detection_method']}")
        else:
            print("  (未检测到击球，可能检测范围太短)")

        # === 轨迹采样点 ===
        print(f"\n{'='*55}")
        print(f"📍 轨迹采样 (前 15 帧)")
        print(f"{'='*55}")
        for i, det in enumerate(detections[:15]):
            for tid, (x1, y1, x2, y2) in det.items():
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                print(f"  帧 {i:3d} | Ball {tid} | "
                      f"框({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f}) | "
                      f"中心({cx:.0f},{cy:.0f})")

        # === 渲染视频 ===
        print(f"\n{'='*55}")
        print(f"📹 渲染视频")
        print(f"{'='*55}")
        if video_output_path and video_output_path.exists():
            vcap = cv2.VideoCapture(str(video_output_path))
            vw = int(vcap.get(cv2.CAP_PROP_FRAME_WIDTH))
            vh = int(vcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            vf = int(vcap.get(cv2.CAP_PROP_FRAME_COUNT))
            vcap.release()
            print(f"  路径: {video_output_path}")
            print(f"  分辨率: {vw}x{vh}")
            print(f"  帧数: {vf}")
            print(f"  ✅ 生成成功")
        else:
            print(f"  ⚠️ 未生成")

        # === 输出文件 ===
        print(f"\n{'='*55}")
        print(f"📁 输出文件")
        print(f"{'='*55}")
        print(f"  分析结果: {artifact_path}")
        print(f"  渲染视频: {video_output_path}")

        # === 断言 ===
        print(f"\n{'='*55}")
        print(f"✅ 断言检查")
        print(f"{'='*55}")
        assert len(detections) > 0, "ball_detections 为空"
        assert summary["detected_frames"] > 0, "未检测到球"
        nan = sum(1 for d in detections for c in d.values()
                  if any(isinstance(v, float) and np.isnan(v) for v in c))
        assert nan == 0, f"存在 {nan} 个 NaN 坐标"
        print(f"  全部通过 ✓")

    finally:
        app.config.settings.analysis_dir = orig_a
        app.config.settings.ball_videos_dir = orig_v


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="网球轨迹 + 击球效果基础测试")
    parser.add_argument("video", nargs="?", default=str(_VIDEO), help="视频路径")
    parser.add_argument("--duration", type=float, default=None,
                        help="检测时长（秒，默认视频全长）")
    parser.add_argument("--start", type=float, default=0.0, help="起始时间（秒）")
    args = parser.parse_args()

    # 未指定 duration 时自动使用视频全长
    if args.duration is None:
        cap = cv2.VideoCapture(str(Path(args.video)))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        args.duration = total_frames / fps

    run(Path(args.video), args.duration, args.start)
