"""
BallTracker 类 - 参考 Tennis_AI_mainland 项目

提供网球检测、轨迹插值和击球检测功能
参考: https://github.com/qchen34/Tennis_AI_mainland
"""

import logging
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str, float | None], None]


class BallTracker:
    """网球追踪器类，封装检测、插值和击球检测功能"""

    def __init__(self, model_path: str | Path):
        """初始化追踪器

        Args:
            model_path: YOLO 模型路径
        """
        self.model_path = Path(model_path)
        self._model = None

    @property
    def model(self):
        """懒加载 YOLO 模型"""
        if self._model is None:
            from ultralytics import YOLO
            logger.info(f"Loading ball detection model from {self.model_path}")
            self._model = YOLO(str(self.model_path))
        return self._model

    def detect_frame(self, frame: np.ndarray) -> dict[int, list[float]]:
        """检测单帧中的网球

        Args:
            frame: 输入帧 (BGR 格式)

        Returns:
            字典，key=1 (track_id), value=[x1, y1, x2, y2] (像素坐标)
        """
        results = self.model.predict(frame, conf=0.15, verbose=False)[0]

        ball_dict = {}
        if results.boxes is not None and len(results.boxes) > 0:
            for box in results.boxes:
                result = box.xyxy.tolist()[0]
                ball_dict[1] = [float(x) for x in result]

        return ball_dict

    def detect_frames(
        self,
        frames: list[np.ndarray],
        read_from_stub: bool = False,
        stub_path: str | Path | None = None,
    ) -> list[dict[int, list[float]]]:
        """批量检测视频帧中的网球

        Args:
            frames: 视频帧列表
            read_from_stub: 是否从缓存读取
            stub_path: 缓存文件路径

        Returns:
            检测结果列表，每帧一个字典 {1: [x1, y1, x2, y2]}
        """
        ball_detections = []

        if read_from_stub and stub_path is not None:
            import pickle
            with open(stub_path, 'rb') as f:
                ball_detections = pickle.load(f)
            return ball_detections

        for frame in frames:
            ball_dict = self.detect_frame(frame)
            ball_detections.append(ball_dict)

        if stub_path is not None:
            import pickle
            with open(stub_path, 'wb') as f:
                pickle.dump(ball_detections, f)

        return ball_detections

    def interpolate_ball_positions(
        self,
        ball_positions: list[dict[int, list[float]]],
    ) -> list[dict[int, list[float]]]:
        """使用 pandas 插值填充缺失的网球位置

        Args:
            ball_positions: 检测结果列表

        Returns:
            插值后的检测结果
        """
        # 提取 track_id=1 的坐标
        positions = [x.get(1, []) for x in ball_positions]

        # 转换为 DataFrame
        df = pd.DataFrame(positions, columns=['x1', 'y1', 'x2', 'y2'])

        # 插值缺失值
        df = df.interpolate()
        df = df.bfill()

        # 转回原格式
        result = [{1: row.tolist()} for row in df.to_numpy()]
        return result

    def get_ball_shot_frames(
        self,
        ball_positions: list[dict[int, list[float]]],
    ) -> list[int]:
        """检测击球帧（通过网球 y 坐标变化方向）

        Args:
            ball_positions: 检测结果列表

        Returns:
            击球帧索引列表
        """
        # 提取 track_id=1 的坐标
        positions = [x.get(1, []) for x in ball_positions]

        if not positions or len(positions) < 10:
            return []

        # 转换为 DataFrame
        df = pd.DataFrame(positions, columns=['x1', 'y1', 'x2', 'y2'])
        df['ball_hit'] = 0

        # 计算中心点 y 坐标
        df['mid_y'] = (df['y1'] + df['y2']) / 2

        # 滚动平均平滑
        df['mid_y_rolling_mean'] = df['mid_y'].rolling(
            window=5, min_periods=1, center=False
        ).mean()

        # 计算 y 方向变化
        df['delta_y'] = df['mid_y_rolling_mean'].diff()

        # 检测方向变化点
        minimum_change_frames_for_hit = 25
        for i in range(1, len(df) - int(minimum_change_frames_for_hit * 1.2)):
            negative_position_change = (
                df['delta_y'].iloc[i] > 0 and df['delta_y'].iloc[i + 1] < 0
            )
            positive_position_change = (
                df['delta_y'].iloc[i] < 0 and df['delta_y'].iloc[i + 1] > 0
            )

            if negative_position_change or positive_position_change:
                change_count = 0
                for change_frame in range(
                    i + 1, i + int(minimum_change_frames_for_hit * 1.2) + 1
                ):
                    if change_frame >= len(df):
                        break
                    negative_following = (
                        df['delta_y'].iloc[i] > 0 and
                        df['delta_y'].iloc[change_frame] < 0
                    )
                    positive_following = (
                        df['delta_y'].iloc[i] < 0 and
                        df['delta_y'].iloc[change_frame] > 0
                    )

                    if negative_position_change and negative_following:
                        change_count += 1
                    elif positive_position_change and positive_following:
                        change_count += 1

                if change_count > minimum_change_frames_for_hit - 1:
                    df.loc[df.index[i], 'ball_hit'] = 1

        # 返回击球帧索引
        shot_frames = df[df['ball_hit'] == 1].index.tolist()
        return shot_frames

    def draw_bboxes(
        self,
        video_frames: list[np.ndarray],
        ball_detections: list[dict[int, list[float]]],
    ) -> list[np.ndarray]:
        """在视频帧上绘制网球边界框

        Args:
            video_frames: 视频帧列表
            ball_detections: 检测结果列表

        Returns:
            绘制后的视频帧列表
        """
        output_frames = []
        for frame, ball_dict in zip(video_frames, ball_detections):
            for track_id, bbox in ball_dict.items():
                x1, y1, x2, y2 = bbox
                # 绘制边界框
                cv2.rectangle(
                    frame,
                    (int(x1), int(y1)),
                    (int(x2), int(y2)),
                    (0, 255, 255),  # 黄色
                    2,
                )
                # 绘制标签
                cv2.putText(
                    frame,
                    f"Ball",
                    (int(x1), int(y1 - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 255, 255),
                    2,
                )
            output_frames.append(frame)
        return output_frames
