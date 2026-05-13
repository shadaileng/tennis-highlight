"""
球员检测与跟踪模块 - 参考 Tennis_AI_mainland 实现

使用 YOLO 模型检测人员并跟踪，过滤出距离场地最近的 2 名球员。
参考: https://github.com/qchen34/Tennis_AI_mainland
"""
import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 默认使用 YOLOv8 nano 模型（轻量快速），首次使用自动下载
PLAYER_MODEL_NAME = "yolov8n.pt"


class PlayerTracker:
    """球员检测与跟踪器"""

    def __init__(self, model_path: str = PLAYER_MODEL_NAME):
        from ultralytics import YOLO
        self.model = YOLO(model_path)

    @staticmethod
    def get_center_of_bbox(bbox):
        x1, y1, x2, y2 = bbox
        return (int((x1 + x2) / 2), int((y1 + y2) / 2))

    @staticmethod
    def get_foot_position(bbox):
        x1, _, x2, y2 = bbox
        return (int((x1 + x2) / 2), int(y2))

    @staticmethod
    def measure_distance(p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5

    def detect_frame(self, frame: np.ndarray) -> dict[int, list[float]]:
        """检测单帧中的球员，返回 {track_id: [x1, y1, x2, y2]}"""
        results = self.model.track(frame, persist=True, verbose=False)[0]
        id_name_dict = results.names
        player_dict = {}
        if results.boxes is not None and len(results.boxes) > 0:
            for box in results.boxes:
                track_id = int(box.id.tolist()[0])
                bbox = box.xyxy.tolist()[0]
                cls_id = int(box.cls.tolist()[0])
                cls_name = id_name_dict[cls_id]
                if cls_name == "person":
                    player_dict[track_id] = bbox
        return player_dict

    def choose_and_filter_players(
        self,
        court_keypoints: np.ndarray,
        player_detections: list[dict[int, list[float]]],
    ) -> list[dict[int, list[float]]]:
        """根据离场地关键点的距离过滤出最近的 2 名球员"""
        if not player_detections or not player_detections[0]:
            logger.warning("No player detections in first frame, cannot filter")
            return player_detections

        chosen_ids = self._choose_players(court_keypoints, player_detections[0])
        logger.info(f"Chosen player track IDs: {chosen_ids}")

        filtered = []
        for det in player_detections:
            filtered_dict = {tid: bbox for tid, bbox in det.items() if tid in chosen_ids}
            filtered.append(filtered_dict)
        return filtered

    def _choose_players(
        self,
        court_keypoints: np.ndarray,
        player_dict: dict[int, list[float]],
    ) -> list[int]:
        """选择距离场地关键点最近的 2 个球员"""
        distances = []
        for track_id, bbox in player_dict.items():
            center = self.get_center_of_bbox(bbox)
            min_dist = float("inf")
            for i in range(0, len(court_keypoints), 2):
                kp = (court_keypoints[i], court_keypoints[i + 1])
                dist = self.measure_distance(center, kp)
                if dist < min_dist:
                    min_dist = dist
            distances.append((track_id, min_dist))

        distances.sort(key=lambda x: x[1])
        return [distances[0][0], distances[1][0]]
