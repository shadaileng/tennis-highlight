"""
场地线关键点检测模块 - 参考 Tennis_AI_mainland 实现

使用 ResNet50 检测网球场的 14 个关键点（28 个坐标值）。
参考: https://github.com/qchen34/Tennis_AI_mainland
"""
import logging
from pathlib import Path

import cv2
import numpy as np
import torch
import torchvision.transforms as transforms
from torchvision import models

logger = logging.getLogger(__name__)

COURT_MODEL_PATH = Path(__file__).parent.parent.parent / "models" / "keypoints_model.pth"


class CourtLineDetector:
    """网球场地线关键点检测器"""

    def __init__(self, model_path: str | Path | None = None):
        model_path = model_path or COURT_MODEL_PATH
        if not Path(model_path).exists():
            raise RuntimeError(
                f"Court line model not found: {model_path}\n"
                f"Please download keypoints_model.pth to models/"
            )

        self.model = models.resnet50(weights=models.ResNet50_Weights.IMAGENET1K_V1)
        self.model.fc = torch.nn.Linear(self.model.fc.in_features, 14 * 2)
        self.model.load_state_dict(torch.load(str(model_path), map_location="cpu"))
        self.model.eval()

        self.transform = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def predict(self, image: np.ndarray) -> np.ndarray:
        """预测 14 个场地关键点坐标，返回 28 维数组"""
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image_tensor = self.transform(image_rgb).unsqueeze(0)
        with torch.no_grad():
            outputs = self.model(image_tensor)
        keypoints = outputs.squeeze().cpu().numpy()
        original_h, original_w = image.shape[:2]
        keypoints[::2] *= original_w / 224.0
        keypoints[1::2] *= original_h / 224.0
        return keypoints

    def draw_keypoints(self, image: np.ndarray, keypoints: np.ndarray) -> np.ndarray:
        """在图像上绘制关键点"""
        for i in range(0, len(keypoints), 2):
            x = int(keypoints[i])
            y = int(keypoints[i + 1])
            cv2.putText(image, str(i // 2), (x, y - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
            cv2.circle(image, (x, y), 5, (0, 0, 255), -1)
        return image
