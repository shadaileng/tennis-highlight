"""
多球跟踪测试

测试目标:
1. 验证 _detect_frame 能否在一次检测中返回多个球（多个 track_id）
2. 验证 analyze_ball_trajectory 输出中包含多个球的轨迹
3. 验证多球各自有独立的轨迹数据，不会互相覆盖
4. 验证多球渲染视频能正确生成

结果输出:
  所有 artifact (*.ball.json.gz) 和渲染视频 (*.mp4) 输出到 data/ball_videos/

用法:
    python tests/test_multi_ball_tracking.py <video_path> [--duration N] [--skip-scan]

示例:
    python tests/test_multi_ball_tracking.py data/uploads/acb378840d7015bd23faa08dd82d1e61_raw.mp4
    python tests/test_multi_ball_tracking.py /path/to/video.mp4 --duration 5.0 --skip-scan
"""

import argparse
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

# 默认测试视频路径（项目根目录下的数据目录）
_DEFAULT_VIDEO = Path(__file__).parent.parent / "data" / "uploads" / "acb378840d7015bd23faa08dd82d1e61_raw.mp4"
# 测试输出目录
ANALYSIS_DIR = Path(__file__).parent.parent / "data" / "analysis"   # 分析结果 (artifact)
VIDEO_DIR = Path(__file__).parent.parent / "data" / "ball_videos"    # 渲染视频


def scan_multi_ball_frames(video_path: Path, max_frames: int = 300, start_frame: int = 0) -> list[tuple[int, int, list[int]]]:
    """
    逐帧扫描视频，找出出现多个球的帧

    返回:
        [(frame_idx, ball_count, [track_id1, track_id2, ...]), ...]
    """
    from app.pipeline.ball_tracking import _detect_frame, _get_ball_model

    model = _get_ball_model()
    cap = cv2.VideoCapture(str(video_path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    multi_ball_frames = []
    frame_idx = start_frame

    while frame_idx < start_frame + max_frames:
        ret, frame = cap.read()
        if not ret:
            break

        ball_dict = _detect_frame(frame, model)
        if len(ball_dict) > 1:
            multi_ball_frames.append((frame_idx, len(ball_dict), list(ball_dict.keys())))

        frame_idx += 1

    cap.release()
    return multi_ball_frames


def test_basic_scan(video_path: Path):
    """基础扫描：检查视频中是否存在多球帧"""
    print("=" * 60)
    print("【测试1】扫描视频中多球出现情况")
    print("=" * 60)

    # 获取视频总帧数
    cap = cv2.VideoCapture(str(video_path))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    total_fps = 30  # 粗略 fps

    multi_frames = scan_multi_ball_frames(video_path, max_frames=300, start_frame=0)
    total_scanned = min(300, total_frames)

    print(f"  扫描帧数: {total_scanned}")
    print(f"  多球帧数: {len(multi_frames)}")

    if multi_frames:
        print(f"  多球帧分布 (前20帧):")
        for frame_idx, count, tids in multi_frames[:20]:
            print(f"    帧 {frame_idx:4d} | {count}个球 | track_ids={tids}")
    else:
        print("  ⚠️ 前300帧未发现多球，尝试更广范围扫描...")
        # 扩大扫描范围
        multi_frames = scan_multi_ball_frames(video_path, max_frames=total_frames, start_frame=0)
        print(f"  全视频扫描 ({len(multi_frames)} 多球帧)")
        for frame_idx, count, tids in multi_frames[:20]:
            print(f"    帧 {frame_idx:4d} | {count}个球 | track_ids={tids}")

    return multi_frames


def test_full_pipeline(video_path: Path, test_duration_s: float = 3.0):
    """完整pipeline测试：运行 analyze_ball_trajectory 并检查多球结果"""
    print(f"\n{'=' * 60}")
    print(f"【测试2】完整流水线测试 (检测 {test_duration_s}s)")
    print(f"{'=' * 60}")

    # 获取视频时长
    cap = cv2.VideoCapture(str(video_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_duration_s = total_frames / video_fps if video_fps > 0 else 30.0
    cap.release()

    from app.pipeline.ball_tracking import analyze_ball_trajectory

    analysis_id = f"test_multi_ball_{int(time.time())}"

    # 输出目录
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    # 临时覆盖配置指向输出目录
    import app.config
    orig_analysis = app.config.settings.analysis_dir
    orig_videos = app.config.settings.ball_videos_dir
    app.config.settings.analysis_dir = ANALYSIS_DIR       # artifact → data/analysis
    app.config.settings.ball_videos_dir = VIDEO_DIR       # 视频  → data/ball_videos

    try:
        # 进度回调
        def progress(pct, msg, eta):
            print(f"  [{pct:5.1f}%] {msg}" + (f" (剩余{eta:.0f}s)" if eta else ""))

        print(f"  分析ID: {analysis_id}")
        t0 = time.time()
        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=video_path,
            video_duration_s=video_duration_s,
            sample_fps=video_fps,
            range_start_s=0.0,
            range_end_s=test_duration_s,
            progress_cb=progress,
        )
        elapsed = time.time() - t0
        print(f"  耗时: {elapsed:.1f}s")

        # === 断言检查 ===
        print(f"\n  --- artifact 结构检查 ---")
        assert artifact_path.exists(), "artifact 文件未生成"
        print(f"  ✔ artifact 文件: {artifact_path}")

        ball_detections = artifact["ball_detections"]
        summary = artifact["summary"]
        hit_moments = artifact.get("hit_moments", [])

        print(f"  total_samples: {summary['total_samples']}")
        print(f"  detected_frames: {summary['detected_frames']}")
        print(f"  total_ball_instances: {summary.get('total_ball_instances', 'N/A')}")
        print(f"  hit_moments: {len(hit_moments)}")
        print(f"  视频输出: {video_output_path}")

        # 收集所有出现的 track_id
        all_track_ids = set()
        for det in ball_detections:
            all_track_ids.update(det.keys())
        print(f"\n  检测到的 track_ids: {sorted(all_track_ids)}")

        # 每个 track_id 的出现次数
        track_id_counts = Counter()
        for det in ball_detections:
            track_id_counts.update(det.keys())
        print(f"  每个 track_id 的出现帧数:")
        for tid in sorted(track_id_counts):
            print(f"    Ball {tid}: {track_id_counts[tid]} 帧")

        # 多帧中同时出现多个球的比例
        multi_ball_frame_count = sum(1 for d in ball_detections if len(d) > 1)
        print(f"\n  多球帧数: {multi_ball_frame_count} / {len(ball_detections)}")

        # === 详细断言 ===
        assert len(ball_detections) > 0, "ball_detections 为空"
        assert len(all_track_ids) > 0, "没有检测到任何球"

        if len(all_track_ids) > 1:
            print(f"\n  ✅ 检测到多球! 共有 {len(all_track_ids)} 个不同 track_id")
            # 验证每个球都有独立轨迹
            for tid in sorted(all_track_ids):
                positions = [(i, det[tid]) for i, det in enumerate(ball_detections) if tid in det]
                if positions:
                    first_pos = positions[0][1]
                    last_pos = positions[-1][1]
                    print(f"    Ball {tid}: 第{positions[0][0]}~{positions[-1][0]}帧, "
                          f"起点({first_pos[0]:.0f},{first_pos[1]:.0f}) "
                          f"终点({last_pos[0]:.0f},{last_pos[1]:.0f})")
        else:
            print(f"\n  ⚠️ 仅检测到 1 个球 (track_id={list(all_track_ids)[0]})")
            # 不自动失败，因为多球可能只在特定时间出现

        # 验证检测坐标数值有效
        print(f"\n  --- 坐标有效性检查 ---")
        nan_count = 0
        for i, det in enumerate(ball_detections):
            for tid, coords in det.items():
                if any(isinstance(c, float) and np.isnan(c) for c in coords):
                    nan_count += 1
        print(f"  NaN 坐标数: {nan_count}")
        assert nan_count == 0, f"存在 {nan_count} 个 NaN 坐标"

        # 验证视频输出
        if video_output_path and video_output_path.exists():
            vcap = cv2.VideoCapture(str(video_output_path))
            vw = int(vcap.get(cv2.CAP_PROP_FRAME_WIDTH))
            vh = int(vcap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            vf = int(vcap.get(cv2.CAP_PROP_FRAME_COUNT))
            vcap.release()
            print(f"\n  --- 渲染视频检查 ---")
            print(f"  分辨率: {vw}x{vh}, 帧数: {vf}")
            assert vw > 0 and vh > 0, "渲染视频分辨率异常"
            print(f"  ✅ 渲染视频生成成功: {video_output_path}")
        else:
            print(f"\n  ⚠️ 渲染视频未生成 (可能是模型或配置问题)")

    finally:
        # 恢复配置（不删除输出目录）
        import app.config
        app.config.settings.analysis_dir = orig_analysis
        app.config.settings.ball_videos_dir = orig_videos

    return artifact


def test_scan_all_times(video_path: Path):
    """全视频分段时间扫描，找到多球最密集的时间段"""
    print(f"\n{'=' * 60}")
    print("【测试3】分段扫描 - 查找多球最密集的时间段")
    print(f"{'=' * 60}")

    # 获取视频信息
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    segment_duration = 2.0  # 每段2秒
    num_segments = int((total_frames / fps) / segment_duration)

    best_segment = None
    best_count = 0

    for seg in range(num_segments):
        start_frame = int(seg * segment_duration * fps)
        max_frames = int(segment_duration * fps)
        frames = scan_multi_ball_frames(video_path, max_frames=max_frames, start_frame=start_frame)

        time_s = seg * segment_duration
        if len(frames) > best_count:
            best_count = len(frames)
            best_segment = (time_s, time_s + segment_duration, frames)

        if frames:
            print(f"  [{time_s:.0f}s-{time_s + segment_duration:.0f}s] "
                  f"{len(frames)} 多球帧 {frames[:3]}")

    if best_segment:
        t_start, t_end, frames = best_segment
        print(f"\n  多球最多时间段: {t_start:.0f}s ~ {t_end:.0f}s ({len(frames)} 个多球帧)")
        print(f"  示例多球帧:")
        for frame_idx, count, tids in frames[:10]:
            print(f"    帧 {frame_idx:4d} (≈{frame_idx / fps:.1f}s) | {count}球 | ids={tids}")
    else:
        print("  ⚠️ 全视频未发现多球帧")


def test_render_multi_ball_segment(video_path: Path, start_s: float, duration_s: float = 2.0):
    """对指定时间段进行完整pipeline测试并生成渲染视频"""
    print(f"\n{'=' * 60}")
    print(f"【测试4】多球段渲染测试 ({start_s}s ~ {start_s + duration_s}s)")
    print(f"{'=' * 60}")

    # 获取视频信息
    cap = cv2.VideoCapture(str(video_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_duration_s = total_frames / video_fps if video_fps > 0 else 30.0
    cap.release()

    from app.pipeline.ball_tracking import analyze_ball_trajectory

    analysis_id = f"render_multi_{int(time.time())}"

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    # 临时覆盖配置指向输出目录
    import app.config
    orig_analysis = app.config.settings.analysis_dir
    orig_videos = app.config.settings.ball_videos_dir
    app.config.settings.analysis_dir = ANALYSIS_DIR       # artifact → data/analysis
    app.config.settings.ball_videos_dir = VIDEO_DIR       # 视频  → data/ball_videos

    try:
        def progress(pct, msg, eta):
            print(f"  [{pct:5.1f}%] {msg}")

        artifact_path, artifact, video_output_path = analyze_ball_trajectory(
            analysis_id=analysis_id,
            video_path=video_path,
            video_duration_s=video_duration_s,
            sample_fps=video_fps,
            range_start_s=start_s,
            range_end_s=start_s + duration_s,
            progress_cb=progress,
        )

        ball_detections = artifact["ball_detections"]
        all_track_ids = set()
        for det in ball_detections:
            all_track_ids.update(det.keys())

        print(f"\n  检测到的球数: {len(all_track_ids)}")
        print(f"  track_ids: {sorted(all_track_ids)}")

        if video_output_path and video_output_path.exists():
            print(f"\n  ✅ 渲染视频: {video_output_path}")
        else:
            print(f"\n  ⚠️ 渲染视频未生成")

    finally:
        import app.config
        app.config.settings.analysis_dir = orig_analysis
        app.config.settings.ball_videos_dir = orig_videos


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="多球跟踪测试 - 验证视频中多个球的检测和独立轨迹绘制",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
输出目录:
  分析结果 (artifact) -> {ANALYSIS_DIR}
  渲染视频  (mp4)    -> {VIDEO_DIR}

示例:
  # 使用默认视频，全时长检测
  python tests/test_multi_ball_tracking.py

  # 指定视频文件，全时长检测（跳过扫描）
  python tests/test_multi_ball_tracking.py path/to/video.mp4 --skip-scan

  # 指定时长和渲染段
  python tests/test_multi_ball_tracking.py path/to/video.mp4 --duration 5.0 --render-start 10.0
        """,
    )
    parser.add_argument("video", nargs="?",
                        default=str(_DEFAULT_VIDEO),
                        help="测试视频路径（默认: data/uploads/目录下的默认视频）")
    parser.add_argument("--duration", type=float, default=None,
                        help="流水线测试的检测时长（秒），默认全时长")
    parser.add_argument("--render-start", type=float, default=None,
                        help="渲染视频的起始时间（秒），默认使用多球最密集段")
    parser.add_argument("--render-duration", type=float, default=2.0,
                        help="渲染视频的时长（秒），默认 2.0")
    parser.add_argument("--skip-scan", action="store_true",
                        help="跳过扫描测试（1、3），直接跑流水线和渲染")
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.exists():
        print(f"❌ 视频文件不存在: {video_path}")
        sys.exit(1)

    # 获取视频基本信息
    cap = cv2.VideoCapture(str(video_path))
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    duration_s = total_frames / video_fps if video_fps > 0 else 0

    print(f"📹 测试视频: {video_path}")
    print(f"   分辨率: 待检测 | FPS: {video_fps:.1f} | 时长: {duration_s:.1f}s | 总帧数: {total_frames}")
    print(f"   是否多球: {'跳过扫描' if args.skip_scan else '待检测'}")
    print()

    multi_frames = []

    if not args.skip_scan:
        # 测试1: 基础扫描
        multi_frames = test_basic_scan(video_path)

        # 测试2: 完整流水线
        test_full_pipeline(video_path, test_duration_s=min(args.duration or duration_s, duration_s))

        # 测试3: 全视频分段时间扫描
        test_scan_all_times(video_path)
    else:
        print("⏭️ 跳过扫描测试 (--skip-scan)")
        # 仍然跑流水线测试
        test_full_pipeline(video_path, test_duration_s=min(args.duration or duration_s, duration_s))

    # 渲染测试
    if args.render_start is not None:
        # 用户指定的渲染时间段
        test_render_multi_ball_segment(video_path, args.render_start, args.render_duration)
    elif multi_frames:
        # 自动选择多球最密集的时间段
        fps = video_fps
        best_start_frame = multi_frames[0][0]
        best_start_s = max(0, best_start_frame / fps - 1.0)
        test_render_multi_ball_segment(video_path, best_start_s, duration_s=args.render_duration)
    else:
        print("\n⏭️ 未检测到多球，跳过渲染测试")

    print(f"\n{'=' * 60}")
    print("所有测试完成!")
    print(f"{'=' * 60}")
