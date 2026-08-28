import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FRIDGE_DATA", str(ROOT / "data")))
CAPTURES_DIR = DATA_DIR / "captures"
DATASET_DIR = DATA_DIR / "dataset"
SAMPLES_DIR = DATASET_DIR / "samples"
GALLERY_PATH = DATASET_DIR / "gallery.npz"
DB_PATH = Path(os.environ.get("FRIDGE_DB", str(DATA_DIR / "fridge.db")))
MODEL_DIR = ROOT / "models"
MODEL_PATH = MODEL_DIR / "yolov5n.onnx"
STATIC_DIR = ROOT / "static"

CAMERA_NAME = "ActionCam_f8160c0282c2"
CAMERA_HOST = "192.168.100.1"
# Generalplus / GoPlus CamPro: ICMP wakeup, then MJPEG over HTTP on :8080.
STREAM_URL = f"http://{CAMERA_HOST}:8080/?action=stream"
SNAPSHOT_URL = f"http://{CAMERA_HOST}:8080/?action=snapshot"

# Official Ultralytics nano weights (ONNX). Downloaded on first scan if missing.
MODEL_URLS = [
    "https://github.com/ultralytics/yolov5/releases/download/v7.0/yolov5n.onnx",
]

DEFAULT_SETTINGS = {
    "camera_name": CAMERA_NAME,
    "camera_host": CAMERA_HOST,
    "stream_url": STREAM_URL,
    "snapshot_url": SNAPSHOT_URL,
    "confidence": "0.35",
    "food_only": "1",
    "custom_threshold": "0.78",
    "use_custom": "1",
}

# YOLO classes that usually need a custom fridge label (milk, juice, yogurt…).
RECLASSIFY_CLASSES = {
    "bottle",
    "wine glass",
    "cup",
    "bowl",
    "vase",
    "refrigerator",
}

COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
]

CATEGORY_BY_CLASS = {
    "banana": "фрукты",
    "apple": "фрукты",
    "orange": "фрукты",
    "broccoli": "овощи",
    "carrot": "овощи",
    "sandwich": "готовое",
    "hot dog": "готовое",
    "pizza": "готовое",
    "donut": "готовое",
    "cake": "готовое",
    "bottle": "напитки",
    "wine glass": "напитки",
    "cup": "напитки",
    "bowl": "посуда",
    "fork": "посуда",
    "knife": "посуда",
    "spoon": "посуда",
}

FOOD_LABELS_RU = {
    "bottle": "Бутылка",
    "wine glass": "Бокал",
    "cup": "Стакан",
    "bowl": "Миска",
    "banana": "Банан",
    "apple": "Яблоко",
    "sandwich": "Сэндвич",
    "orange": "Апельсин",
    "broccoli": "Брокколи",
    "carrot": "Морковь",
    "hot dog": "Хот-дог",
    "pizza": "Пицца",
    "donut": "Пончик",
    "cake": "Торт",
    "fork": "Вилка",
    "knife": "Нож",
    "spoon": "Ложка",
    "refrigerator": "Холодильник",
    "vase": "Банка / ваза",
    "wine bottle": "Бутылка",
}
