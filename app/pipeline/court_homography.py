"""
球场单应性矩阵模块 - 手动 4 角点 → Homography → 14 关键点

替代 ResNet50 自动检测，通过用户标记的 4 个球场外角点计算透视变换，
将标准球场 14 关键点投影到图像坐标系，精度远超自动检测。

用法:
    from app.pipeline.court_homography import calibration_to_keypoints
    
    # 4 个用户点击的归一化角点
    points = [
        {"x": 0.12, "y": 0.75},   # near-left (bottom-left)
        {"x": 0.88, "y": 0.72},   # near-right (bottom-right)
        {"x": 0.32, "y": 0.12},   # far-right (top-right)
        {"x": 0.68, "y": 0.10},   # far-left (top-left)
    ]
    keypoints = calibration_to_keypoints(points, width, height)
    # 返回 28 维数组，与 CourtLineDetector.predict() 格式一致
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 标准球场外角尺寸（米）
DOUBLE_LINE_WIDTH = 10.97   # 双打场地宽度
COURT_LENGTH = 23.77        # 底线到底线长度
DOUBLE_ALLY_DIFFERENCE = 1.37  # 双打边线到单打边线距离
SINGLE_LINE_WIDTH = 8.23    # 单打场地宽度
NO_MANS_LAND_HEIGHT = 5.48  # 底线到发球线距离（23.77/2 - 6.4）
HALF_COURT_LINE_HEIGHT = 11.88  # 半场长度

# 14 个标准关键点在球场坐标系中的位置（米）
# 坐标系：原点 (0,0) 为左上外角（远侧左），y 向下递增
# 对应 CourtLineDetector 的 14 关键点输出格式
_STANDARD_14_KP_METERS = np.array([
    [0, 0],              # 0: top-left outer (far-left)
    [DOUBLE_LINE_WIDTH, 0],                         # 1: top-right outer (far-right)
    [0, COURT_LENGTH],                              # 2: bottom-left outer (near-left)
    [DOUBLE_LINE_WIDTH, COURT_LENGTH],              # 3: bottom-right outer (near-right)
    [DOUBLE_ALLY_DIFFERENCE, 0],                    # 4: top-left singles
    [DOUBLE_ALLY_DIFFERENCE, COURT_LENGTH],         # 5: bottom-left singles
    [DOUBLE_LINE_WIDTH - DOUBLE_ALLY_DIFFERENCE, 0],           # 6: top-right singles
    [DOUBLE_LINE_WIDTH - DOUBLE_ALLY_DIFFERENCE, COURT_LENGTH], # 7: bottom-right singles
    [DOUBLE_ALLY_DIFFERENCE, NO_MANS_LAND_HEIGHT],             # 8: left service line top
    [DOUBLE_LINE_WIDTH - DOUBLE_ALLY_DIFFERENCE, NO_MANS_LAND_HEIGHT],    # 9: right service line top
    [DOUBLE_ALLY_DIFFERENCE, COURT_LENGTH - NO_MANS_LAND_HEIGHT],         # 10: left service line bottom
    [DOUBLE_LINE_WIDTH - DOUBLE_ALLY_DIFFERENCE, COURT_LENGTH - NO_MANS_LAND_HEIGHT],  # 11: right service line bottom
    [DOUBLE_LINE_WIDTH / 2, NO_MANS_LAND_HEIGHT],              # 12: net top center
    [DOUBLE_LINE_WIDTH / 2, COURT_LENGTH - NO_MANS_LAND_HEIGHT],  # 13: net bottom center
], dtype=np.float32)


def calibration_to_keypoints(
    calibration_points: list[dict],
    image_width: int,
    image_height: int,
) -> np.ndarray:
    """
    将用户标记的 4 个球场外角点转换为 14 个关键点坐标。

    使用 Homography 矩阵将标准球场坐标系的 14 关键点投影到图像坐标系。

    Args:
        calibration_points: 4 个归一化角点 [{"x": 0-1, "y": 0-1}, ...]
            顺序约定: near-left, near-right, far-right, far-left
        image_width: 视频帧宽度（像素）
        image_height: 视频帧高度（像素）

    Returns:
        28 维 numpy 数组 [x0, y0, x1, y1, ..., x13, y13]
        格式与 CourtLineDetector.predict() 完全一致

    Raises:
        ValueError: 角点数量不足或坐标无效时
    """
    if len(calibration_points) != 4:
        raise ValueError(f"需要 4 个角点，但收到 {len(calibration_points)} 个")

    # 1. 将归一化坐标转为像素坐标
    pixel_pts = np.array([
        [p["x"] * image_width, p["y"] * image_height]
        for p in calibration_points
    ], dtype=np.float32)

    # 2. 标准球场中对应的 4 个外角坐标（米）
    #    用户点顺序: near-left, near-right, far-right, far-left
    #    对应标准关键点索引: 2, 3, 1, 0
    standard_pts = np.array([
        [0, COURT_LENGTH],                          # near-left  → bottom-left outer (kp 2)
        [DOUBLE_LINE_WIDTH, COURT_LENGTH],          # near-right → bottom-right outer (kp 3)
        [DOUBLE_LINE_WIDTH, 0],                     # far-right  → top-right outer (kp 1)
        [0, 0],                                     # far-left   → top-left outer (kp 0)
    ], dtype=np.float32)

    # 3. 计算 Homography 矩阵（标准坐标 → 像素坐标）
    H, mask = cv2.findHomography(standard_pts, pixel_pts)
    if H is None:
        raise ValueError("Homography 计算失败：角点可能共线")

    # 4. 将所有 14 个标准关键点投影到图像坐标系
    #    cv2.perspectiveTransform 需要 Nx1x2 形状
    src = _STANDARD_14_KP_METERS.reshape(-1, 1, 2).astype(np.float32)
    projected = cv2.perspectiveTransform(src, H)
    projected = projected.reshape(-1, 2)

    # 5. 展平为 28 维数组 [x0, y0, x1, y1, ...]
    keypoints = np.zeros(28, dtype=np.float32)
    keypoints[0::2] = projected[:, 0]
    keypoints[1::2] = projected[:, 1]

    # 6. 裁剪到图像边界（防止部分内场关键点因透视投影跑到画面外）
    keypoints[0::2] = np.clip(keypoints[0::2], 0, image_width)
    keypoints[1::2] = np.clip(keypoints[1::2], 0, image_height)

    logger.info(
        "Homography 计算完成: 4角点→14关键点, "
        "角点像素坐标=%s, 内点数=%d",
        pixel_pts.tolist(), int(mask.sum()) if mask is not None else 4,
    )

    return keypoints


def get_homography_matrix(
    calibration_points: list[dict],
    image_width: int,
    image_height: int,
) -> np.ndarray:
    """
    返回 Homography 矩阵（3x3），用于任意像素↔米坐标转换。

    H 将球场标准坐标（米）映射到图像像素坐标。
    可用于将任意标准球场位置投影到图像上。

    反向映射（图像→米）: np.linalg.inv(H)
    """
    if len(calibration_points) != 4:
        raise ValueError(f"需要 4 个角点，但收到 {len(calibration_points)} 个")

    pixel_pts = np.array([
        [p["x"] * image_width, p["y"] * image_height]
        for p in calibration_points
    ], dtype=np.float32)

    standard_pts = np.array([
        [0, COURT_LENGTH],
        [DOUBLE_LINE_WIDTH, COURT_LENGTH],
        [DOUBLE_LINE_WIDTH, 0],
        [0, 0],
    ], dtype=np.float32)

    H, _ = cv2.findHomography(standard_pts, pixel_pts)
    return H


def pixel_to_meters(
    pixel_x: float, pixel_y: float,
    H_inv: np.ndarray,
) -> tuple[float, float]:
    """
    将图像像素坐标转换为球场米坐标。

    Args:
        pixel_x, pixel_y: 图像像素坐标
        H_inv: Homography 逆矩阵（图像→米）

    Returns:
        (court_x_m, court_y_m): 球场米坐标
    """
    pt = np.array([[[pixel_x, pixel_y]]], dtype=np.float32)
    court_pt = cv2.perspectiveTransform(pt, H_inv)
    return court_pt[0, 0, 0], court_pt[0, 0, 1]


def get_standard_keypoints() -> np.ndarray:
    """返回标准 14 关键点的米坐标（28 维数组）"""
    kp = np.zeros(28, dtype=np.float32)
    kp[0::2] = _STANDARD_14_KP_METERS[:, 0]
    kp[1::2] = _STANDARD_14_KP_METERS[:, 1]
    return kp
