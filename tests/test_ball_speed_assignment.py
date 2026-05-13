"""
球速归属测试 - 检测球速标注是否显示在正确的球员身上

问题描述:
  有两处球速的绘制对象错了 - 球在近端球员附近击出，
  但球速文字却显示在远端球员头顶。

测试方法:
  1. 运行完整 pipeline 并提取 speed_events
  2. 检查每个 ball_speed.hitter_id 是否等于 near_player_id
  3. 在击球时刻提取帧截图并标注调试信息
  4. 检查 ball_mini_coords 中 track_id 是否匹配

用法:
  python tests/test_ball_speed_assignment.py data/uploads/input_video.mp4 --duration 15.0

示例:
  python tests/test_ball_speed_assignment.py data/uploads/input_video.mp4 --duration 10.0
"""

import argparse
import time
import sys
from pathlib import Path

import cv2
import numpy as np

# ── 测试输出目录 ──
SAVE_DIR = Path(__file__).parent.parent / "data" / "ball_speed_debug"
ANALYSIS_DIR = Path(__file__).parent.parent / "data" / "analysis"
VIDEO_DIR = Path(__file__).parent.parent / "data" / "ball_videos"


def analyze_speed_assignment(video_path: Path, test_duration_s: float, save_dir: Path):
    """核心测试：分析球速归属是否正确"""
    print("=" * 70)
    print("🏓 球速归属正确性诊断测试")
    print("=" * 70)

    # ── 获取视频信息 ──
    cap = cv2.VideoCapture(str(video_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_duration_s = total_frames / video_fps if video_fps > 0 else 0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    print(f"📹 视频: {video_path}")
    print(f"   分辨率: {width}x{height} | FPS: {video_fps:.1f} | 时长: {video_duration_s:.1f}s")

    # ── 运行 pipeline ──
    from app.pipeline.ball_tracking import analyze_ball_trajectory

    analysis_id = f"speed_assignment_{int(time.time())}"

    import app.config
    orig_analysis = app.config.settings.analysis_dir
    orig_videos = app.config.settings.ball_videos_dir
    app.config.settings.analysis_dir = ANALYSIS_DIR
    app.config.settings.ball_videos_dir = VIDEO_DIR

    artifact = None
    try:
        def progress(pct, msg, eta):
            print(f"  [{pct:5.1f}%] {msg}")
            sys.stdout.flush()

        print(f"\n📊 运行 pipeline (检测 {min(test_duration_s, video_duration_s):.1f}s)...")
        t0 = time.time()
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=video_path,
            video_duration_s=video_duration_s,
            sample_fps=video_fps,
            range_start_s=0.0,
            range_end_s=min(test_duration_s, video_duration_s),
            progress_cb=progress,
        )
        elapsed = time.time() - t0
        print(f"  耗时: {elapsed:.1f}s")
    finally:
        app.config.settings.analysis_dir = orig_analysis
        app.config.settings.ball_videos_dir = orig_videos

    if artifact is None:
        print("❌ pipeline 执行失败")
        return

    # ── 提取关键数据 ──
    hit_moments = artifact.get("hit_moments", [])
    player_detections = artifact.get("player_detections", [])
    speed_events = artifact.get("speed_events")
    metadata = artifact.get("metadata", {})

    print(f"\n📋 pipeline 输出摘要:")
    print(f"  hit_moments: {len(hit_moments)} 个")

    if not speed_events:
        print("❌ 未生成 speed_events")
        return

    near_player_id = speed_events.get("near_player_id")
    ball_speeds = speed_events.get("ball_speeds", [])
    player_speeds = speed_events.get("player_speeds", [])

    print(f"\n🔍 speed_events 诊断:")
    print(f"  near_player_id = {near_player_id}")
    print(f"  ball_speeds 条数 = {len(ball_speeds)}")
    print(f"  player_speeds 条数 = {len(player_speeds)}")

    # ── 检查 1: 场地关键点分析 ──
    print(f"\n{'─' * 70}")
    print("[检查 1] 球员身份分配分析")
    print(f"{'─' * 70}")

    court_keypoints = artifact.get("court_keypoints")
    if court_keypoints and len(court_keypoints) >= 28:
        net_y = (court_keypoints[25] + court_keypoints[27]) / 2.0
        print(f"  网线 y 分界线 (keypoint 12/13 均值) = {net_y:.1f}")

        near_foot_ys = []
        far_foot_ys = []
        far_id = None
        for det in player_detections:
            if len(det) >= 2:
                for pid, bbox in det.items():
                    foot_y = bbox[3]
                    if pid == near_player_id:
                        near_foot_ys.append(foot_y)
                    else:
                        far_foot_ys.append(foot_y)
                        far_id = pid

        if near_foot_ys:
            near_mean = np.mean(near_foot_ys)
            print(f"  近端 P{near_player_id}: foot_y 均值={near_mean:.0f} "
                  f"(范围 [{min(near_foot_ys):.0f}, {max(near_foot_ys):.0f}])")
        if far_foot_ys:
            far_mean = np.mean(far_foot_ys)
            print(f"  远端 P{far_id}: foot_y 均值={far_mean:.0f} "
                  f"(范围 [{min(far_foot_ys):.0f}, {max(far_foot_ys):.0f}])")
            verdict = "✅ 正确" if near_mean > far_mean else "❌ 疑似错误"
            print(f"  近端 foot_y > 远端 foot_y? {verdict}")
            verdict2 = "✅ 正确" if near_mean > net_y and far_mean < net_y else "❌ 疑似错误"
            print(f"  近端在网线下、远端在网线上? {verdict2}")
    else:
        print(f"  ⚠️ 无场地关键点数据")

    # ── 检查 2: 每个 ball_speed 的归属 ──
    print(f"\n{'─' * 70}")
    print("[检查 2] ball_speed 归属正确性")
    print(f"{'─' * 70}")

    misattributed = []
    correct_count = 0

    for i, bs in enumerate(ball_speeds):
        hitter = bs.get("hitter_id")
        frame_idx = bs.get("frame_idx")
        speed = bs.get("speed_ms")

        is_correct = (hitter == near_player_id)
        status = "✅" if is_correct else "❌"
        if is_correct:
            correct_count += 1
        else:
            misattributed.append((i, bs))

        ball_pos = bs.get("ball_pos")
        pos_str = f"({ball_pos[0]:.0f},{ball_pos[1]:.0f})" if ball_pos else "N/A"
        print(f"  {status} frame={frame_idx}, speed={speed}m/s, "
              f"hitter=P{hitter}, near=P{near_player_id}, ball_pos={pos_str}")

    total_bs = len(ball_speeds)
    if total_bs > 0:
        print(f"\n  ✅ 正确: {correct_count}/{total_bs}  ❌ 错误: {len(misattributed)}/{total_bs}")

    # ── 检查 3: ball_mini_coords 的 track_id 完整性 ──
    print(f"\n{'─' * 70}")
    print("[检查 3] ball_mini_coords track_id 分布")
    print(f"{'─' * 70}")
    
    ball_mini_coords = artifact.get("ball_mini_coords", [])
    if ball_mini_coords:
        track_id_counts = {}
        for entry in ball_mini_coords:
            for tid in entry.keys():
                track_id_counts[tid] = track_id_counts.get(tid, 0) + 1
        print(f"  ball_mini_coords 中的 track_id 分布:")
        for tid in sorted(track_id_counts):
            print(f"    track_id={tid}: {track_id_counts[tid]} 帧")
        
        # 检查 ball_speeds 对应的帧，验证 track_id=1 是否存在
        for i, bs in enumerate(ball_speeds[:5]):
            frame_idx = bs.get("frame_idx")
            if frame_idx < len(ball_mini_coords):
                coords = ball_mini_coords[frame_idx]
                print(f"  ball_speed[{i}] 帧 {frame_idx}: mini_coords_keys={list(coords.keys())}")
    else:
        print("  ⚠️ artifact 中无 ball_mini_coords 数据")

    # ── 检查 4: 生成调试帧截图 ──
    print(f"\n{'─' * 70}")
    print("[检查 4] 生成调试帧截图")
    print(f"{'─' * 70}")

    if misattributed:
        save_dir.mkdir(parents=True, exist_ok=True)
        print(f"  输出目录: {save_dir}")

        cap = cv2.VideoCapture(str(video_path))
        # 同时也读取正确的条目做对比
        all_bs_to_capture = [bs for _, bs in misattributed]
        # 取前2个正确条目做对比
        correct_samples = [bs for i, bs in enumerate(ball_speeds) if bs.get("hitter_id") == near_player_id][:2]
        all_bs_to_capture.extend(correct_samples)

        for bs in all_bs_to_capture:
            frame_idx = bs.get("frame_idx", 0)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                continue

            ball_pos = bs.get("ball_pos", (0, 0))
            hitter_id = bs.get("hitter_id")

            # 获取当前帧球员检测
            current_players = player_detections[frame_idx] if frame_idx < len(player_detections) else {}

            # 绘制调试信息
            h, w = frame.shape[:2]
            for pid, bbox in current_players.items():
                x1, y1, x2, y2 = [int(v) for v in bbox]
                color = (0, 255, 0) if pid == near_player_id else (0, 0, 255)
                hitter_tag = " [HITTER]" if pid == hitter_id else ""
                tag = f"P{pid}{' [NEAR]' if pid == near_player_id else ' [FAR]'}{hitter_tag}"
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, tag, (x1, y1 - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                # foot_y
                cv2.circle(frame, ((x1 + x2)//2, y2), 6, (255, 255, 0), -1)
                cv2.putText(frame, f"foot_y={y2}", ((x1 + x2)//2 + 10, y2 - 5),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

            # 球位置
            if ball_pos and len(ball_pos) >= 2:
                bx, by = int(ball_pos[0]), int(ball_pos[1])
                cv2.circle(frame, (bx, by), 10, (0, 0, 255), -1)
                cv2.putText(frame, f"Ball", (bx + 12, by),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

            # 头部信息
            cv2.putText(frame, f"Frame {frame_idx} | "
                        f"speed={bs['speed_ms']}m/s | "
                        f"hitter=P{hitter_id} | near=P{near_player_id}",
                        (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            is_correct = (hitter_id == near_player_id)
            prefix = "CORRECT" if is_correct else "WRONG"
            out_path = save_dir / f"{prefix}_frame_{frame_idx:06d}_hitter_{hitter_id}.jpg"
            cv2.imwrite(str(out_path), frame)
            print(f"  📸 {'✅' if is_correct else '❌'} {out_path.name}")

        cap.release()
    else:
        print("  ✅ 没有错误归属的条目")

    # ── 结果汇总 ──
    print(f"\n{'=' * 70}")
    if misattributed:
        print(f"❌ 测试失败: {len(misattributed)}/{total_bs} 个球速条目归属错误!")
        for _, bs in misattributed:
            print(f"    frame={bs['frame_idx']}, speed={bs['speed_ms']}m/s, "
                  f"hitter=P{bs.get('hitter_id')}, near=P{near_player_id}")
        print(f"   调试图片: {save_dir}")
    else:
        print(f"✅ 测试通过! 所有 {total_bs} 个球速条目归属于正确的球员")

    print(f"{'=' * 70}")

    # 清理 artifact
    if artifact_path and artifact_path.exists():
        artifact_path.unlink()
        print(f"  已清理临时 artifact")

    print(f"\n🏓 测试完成!")


def main():
    parser = argparse.ArgumentParser(
        description="球速归属正确性测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("video", type=str, help="输入视频路径")
    parser.add_argument("--duration", type=float, default=15.0,
                        help="检测时长（秒），默认 15.0")
    parser.add_argument("--save-dir", type=str, default=str(SAVE_DIR),
                        help="调试图片保存目录")
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.exists():
        print(f"❌ 文件不存在: {args.video}")
        sys.exit(1)

    analyze_speed_assignment(video_path, args.duration, Path(args.save_dir))


if __name__ == "__main__":
    main()
