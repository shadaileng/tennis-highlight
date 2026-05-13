"""
小场地映射模块 - 参考 Tennis_AI_mainland 实现

建立从原始视频像素坐标系到小场地俯视图的映射，
利用球员身高作为比例尺实现像素 ↔ 米的双向转换。
参考: https://github.com/qchen34/Tennis_AI_mainland
"""
import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 球场实际尺寸常量（米）
SINGLE_LINE_WIDTH = 8.23
DOUBLE_LINE_WIDTH = 10.97
HALF_COURT_LINE_HEIGHT = 11.88
SERVICE_LINE_WIDTH = 6.4
DOUBLE_ALLY_DIFFERENCE = 1.37
NO_MANS_LAND_HEIGHT = 5.48

# 默认球员身高（米）
PLAYER_1_HEIGHT_METERS = 1.88
PLAYER_2_HEIGHT_METERS = 1.91


class MiniCourt:
    """
    小场地坐标系管理器

    在小场地画布（250x500px）上建立网球场俯视图，
    提供像素 ↔ 米的双向转换功能。
    """

    def __init__(self, frame: np.ndarray):
        self.drawing_rectangle_width = 250
        self.drawing_rectangle_height = 500
        self.buffer = 50
        self.padding_court = 20

        self._set_canvas_background_box_position(frame)
        self._set_mini_court_position()
        self._set_court_drawing_key_points()
        self._set_court_lines()

    def convert_meters_to_pixels(self, meters: float) -> float:
        return (meters * self.court_drawing_width) / DOUBLE_LINE_WIDTH

    def get_width_of_mini_court(self) -> int:
        return self.court_drawing_width

    def get_court_drawing_keypoints(self) -> list:
        return self.drawing_key_points

    def _set_court_drawing_key_points(self):
        """建立小场地 14 个关键点的绘制坐标"""
        drawing_key_points = [0] * 28

        # point 0 - top-left court corner
        drawing_key_points[0] = int(self.court_start_x)
        drawing_key_points[1] = int(self.court_start_y)
        # point 1 - top-right court corner
        drawing_key_points[2] = int(self.court_end_x)
        drawing_key_points[3] = int(self.court_start_y)
        # point 2 - bottom-left court corner
        drawing_key_points[4] = int(self.court_start_x)
        drawing_key_points[5] = self.court_start_y + int(self.convert_meters_to_pixels(HALF_COURT_LINE_HEIGHT * 2))
        # point 3 - bottom-right court corner
        drawing_key_points[6] = drawing_key_points[0] + self.court_drawing_width
        drawing_key_points[7] = drawing_key_points[5]
        # point 4
        drawing_key_points[8] = drawing_key_points[0] + int(self.convert_meters_to_pixels(DOUBLE_ALLY_DIFFERENCE))
        drawing_key_points[9] = drawing_key_points[1]
        # point 5
        drawing_key_points[10] = drawing_key_points[4] + int(self.convert_meters_to_pixels(DOUBLE_ALLY_DIFFERENCE))
        drawing_key_points[11] = drawing_key_points[5]
        # point 6
        drawing_key_points[12] = drawing_key_points[2] - int(self.convert_meters_to_pixels(DOUBLE_ALLY_DIFFERENCE))
        drawing_key_points[13] = drawing_key_points[3]
        # point 7
        drawing_key_points[14] = drawing_key_points[6] - int(self.convert_meters_to_pixels(DOUBLE_ALLY_DIFFERENCE))
        drawing_key_points[15] = drawing_key_points[7]
        # point 8
        drawing_key_points[16] = drawing_key_points[8]
        drawing_key_points[17] = drawing_key_points[9] + int(self.convert_meters_to_pixels(NO_MANS_LAND_HEIGHT))
        # point 9
        drawing_key_points[18] = drawing_key_points[16] + int(self.convert_meters_to_pixels(SINGLE_LINE_WIDTH))
        drawing_key_points[19] = drawing_key_points[17]
        # point 10
        drawing_key_points[20] = drawing_key_points[10]
        drawing_key_points[21] = drawing_key_points[11] - int(self.convert_meters_to_pixels(NO_MANS_LAND_HEIGHT))
        # point 11
        drawing_key_points[22] = drawing_key_points[20] + int(self.convert_meters_to_pixels(SINGLE_LINE_WIDTH))
        drawing_key_points[23] = drawing_key_points[21]
        # point 12
        drawing_key_points[24] = int((drawing_key_points[16] + drawing_key_points[18]) / 2)
        drawing_key_points[25] = drawing_key_points[17]
        # point 13
        drawing_key_points[26] = int((drawing_key_points[20] + drawing_key_points[22]) / 2)
        drawing_key_points[27] = drawing_key_points[21]

        self.drawing_key_points = drawing_key_points

    def _set_court_lines(self):
        """设置场地线连接关系"""
        self.lines = [
            (0, 2), (4, 5), (6, 7), (1, 3),
            (0, 1), (8, 9), (10, 11), (2, 3),
        ]

    def _set_mini_court_position(self):
        self.court_start_x = self.start_x + self.padding_court
        self.court_start_y = self.start_y + self.padding_court
        self.court_end_x = self.end_x - self.padding_court
        self.court_end_y = self.end_y - self.padding_court
        self.court_drawing_width = self.court_end_x - self.court_start_x

    def _set_canvas_background_box_position(self, frame):
        """设置小场地画布在视频帧中的位置（右下角）"""
        self.end_x = frame.shape[1] - self.buffer
        self.end_y = self.buffer + self.drawing_rectangle_height
        self.start_x = self.end_x - self.drawing_rectangle_width
        self.start_y = self.end_y - self.drawing_rectangle_height

    def convert_bounding_boxes_to_mini_court_coordinates(
        self,
        player_boxes: list[dict[int, list[float]]],
        ball_boxes: list[dict[int, list[float]]],
        original_court_key_points: list | np.ndarray,
    ) -> tuple[list[dict[int, tuple]], list[dict[int, tuple]]]:
        """
        将球员和球的边界框映射到小场地坐标

        Args:
            player_boxes: 逐帧球员检测框 [{track_id: [x1,y1,x2,y2]}, ...]
            ball_boxes: 逐帧球检测框 [{1: [x1,y1,x2,y2]}, ...]
            original_court_key_points: 原始帧上的 14 个关键点坐标 (28 维)

        Returns:
            (player_mini_coords, ball_mini_coords)
        """
        player_heights = {
            1: PLAYER_1_HEIGHT_METERS,
            2: PLAYER_2_HEIGHT_METERS,
        }

        output_player_boxes = []
        output_ball_boxes = []

        for frame_num, player_bbox in enumerate(player_boxes):
            # 找到离球最近的球员
            closest_player_id_to_ball = None
            ball_center = None
            if player_bbox and frame_num < len(ball_boxes) and ball_boxes[frame_num]:
                ball_bbox = ball_boxes[frame_num].get(1)
                if ball_bbox is not None:
                    ball_center = self._get_center_of_bbox(ball_bbox)
                    closest_player_id_to_ball = min(
                        player_bbox.keys(),
                        key=lambda pid: self._measure_distance(
                            ball_center, self._get_center_of_bbox(player_bbox[pid])
                        ),
                    )

            output_player_bboxes_dict = {}
            # 缓存本帧球的 mini-court 坐标（仅在球员循环中计算一次）
            ball_mini_pos_for_frame = None
            for player_id, bbox in player_bbox.items():
                foot_position = self._get_foot_position(bbox)

                closest_kp_index = self._get_closest_keypoint_index(
                    foot_position, original_court_key_points, [0, 2, 12, 13]
                )
                closest_kp = (
                    original_court_key_points[closest_kp_index * 2],
                    original_court_key_points[closest_kp_index * 2 + 1],
                )

                # 窗口内最大球员高度
                frame_min = max(0, frame_num - 20)
                frame_max = min(len(player_boxes), frame_num + 50)
                heights = [
                    self._get_height_of_bbox(player_boxes[i].get(player_id, [0, 0, 0, 0]))
                    for i in range(frame_min, frame_max)
                    if player_id in player_boxes[i]
                ]
                max_height_px = max(heights) if heights else 100

                mini_pos = self._get_mini_court_coordinates(
                    foot_position, closest_kp, closest_kp_index,
                    max_height_px, player_heights.get(player_id, 1.88),
                )
                output_player_bboxes_dict[player_id] = mini_pos

                # 球映射（仅对离球最近的球员计算一次，暂存到变量中）
                if (
                    closest_player_id_to_ball == player_id
                    and ball_center is not None
                    and frame_num < len(ball_boxes)
                    and ball_boxes[frame_num].get(1) is not None
                ):
                    ball_kp_index = self._get_closest_keypoint_index(
                        ball_center, original_court_key_points, [0, 2, 12, 13]
                    )
                    ball_kp = (
                        original_court_key_points[ball_kp_index * 2],
                        original_court_key_points[ball_kp_index * 2 + 1],
                    )
                    ball_mini_pos_for_frame = self._get_mini_court_coordinates(
                        ball_center, ball_kp, ball_kp_index,
                        max_height_px, player_heights.get(player_id, 1.88),
                    )

            # 每帧只 append 一次球坐标（修复：球坐标条目数与帧数一致）
            if ball_mini_pos_for_frame is not None:
                output_ball_boxes.append({1: ball_mini_pos_for_frame})
            else:
                output_ball_boxes.append({1: (0, 0)} if frame_num < len(output_player_boxes) else {})

            if not output_player_bboxes_dict:
                output_player_boxes.append({})
            else:
                output_player_boxes.append(output_player_bboxes_dict)

        return output_player_boxes, output_ball_boxes

    def _get_mini_court_coordinates(
        self,
        object_position: tuple,
        closest_key_point: tuple,
        closest_key_point_index: int,
        player_height_in_pixels: float,
        player_height_in_meters: float,
    ) -> tuple:
        """将原始帧坐标转换为小场地坐标"""
        dx_px = abs(object_position[0] - closest_key_point[0])
        dy_px = abs(object_position[1] - closest_key_point[1])

        dx_m = (dx_px * player_height_in_meters) / player_height_in_pixels if player_height_in_pixels > 0 else 0
        dy_m = (dy_px * player_height_in_meters) / player_height_in_pixels if player_height_in_pixels > 0 else 0

        dx_mini = self.convert_meters_to_pixels(dx_m)
        dy_mini = self.convert_meters_to_pixels(dy_m)

        mini_kp = (
            self.drawing_key_points[closest_key_point_index * 2],
            self.drawing_key_points[closest_key_point_index * 2 + 1],
        )

        return (mini_kp[0] + dx_mini, mini_kp[1] + dy_mini)

    @staticmethod
    def _get_center_of_bbox(bbox):
        return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

    @staticmethod
    def _get_foot_position(bbox):
        return ((bbox[0] + bbox[2]) / 2, bbox[3])

    @staticmethod
    def _get_height_of_bbox(bbox):
        return bbox[3] - bbox[1]

    @staticmethod
    def _get_closest_keypoint_index(point, keypoints, keypoint_indices):
        closest_dist = float("inf")
        closest_idx = keypoint_indices[0]
        for idx in keypoint_indices:
            kp = (keypoints[idx * 2], keypoints[idx * 2 + 1])
            dist = abs(point[1] - kp[1])
            if dist < closest_dist:
                closest_dist = dist
                closest_idx = idx
        return closest_idx

    @staticmethod
    def _measure_distance(p1, p2):
        return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5
