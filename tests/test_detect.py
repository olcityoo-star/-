import numpy as np
import pytest

from fridge.detect import parse_predictions


def test_parse_yolov8_shape():
    pred = np.zeros((1, 84, 100), dtype=np.float32)
    pred[0, 0, 0] = 100
    pred[0, 1, 0] = 100
    pred[0, 2, 0] = 20
    pred[0, 3, 0] = 20
    pred[0, 4 + 46, 0] = 0.9  # banana
    boxes, scores, class_ids = parse_predictions(pred)
    assert boxes.shape[1] == 4
    assert int(class_ids[0]) == 46
    assert scores[0] == pytest.approx(0.9)


def test_parse_yolov5_shape():
    pred = np.zeros((1, 5, 85), dtype=np.float32)
    pred[0, 0, :4] = [50, 50, 10, 10]
    pred[0, 0, 4] = 0.8
    pred[0, 0, 5 + 47] = 0.9  # apple
    boxes, scores, class_ids = parse_predictions(pred)
    assert int(class_ids[0]) == 47
    assert scores[0] == pytest.approx(0.72)
