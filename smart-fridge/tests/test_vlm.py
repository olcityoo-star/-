import pytest

from app.detectors.base import DetectorError
from app.detectors.vlm import parse_items


def test_parses_plain_json():
    detections = parse_items('{"items": [{"name": "молоко", "count": 1, "confidence": 0.9}]}')
    assert [(d.key, d.confidence) for d in detections] == [("milk", 0.9)]


def test_parses_json_wrapped_in_markdown_and_prose():
    content = 'Вот что я вижу:\n```json\n{"items": [{"name": "сыр", "count": 2}]}\n```\nГотово.'
    detections = parse_items(content)
    assert [d.key for d in detections] == ["cheese", "cheese"]


def test_count_expands_into_separate_detections():
    detections = parse_items('{"items": [{"name": "помидор", "count": 3}]}')
    assert len(detections) == 3
    assert {d.key for d in detections} == {"tomato"}


def test_content_blocks_from_anthropic_style_api():
    detections = parse_items([{"type": "text", "text": '{"items": [{"name": "яйца"}]}'}])
    assert [d.key for d in detections] == ["eggs"]


def test_broken_values_fall_back_to_defaults():
    detections = parse_items('{"items": [{"name": "хлеб", "count": "две", "confidence": "высокая"}]}')
    assert len(detections) == 1
    assert detections[0].confidence == pytest.approx(0.8)


def test_non_food_answers_are_dropped():
    detections = parse_items('{"items": [{"name": "полка"}, {"name": "рука"}, {"name": "сок"}]}')
    assert [d.key for d in detections] == ["juice"]


def test_absurd_counts_are_capped():
    detections = parse_items('{"items": [{"name": "яйца", "count": 500}]}')
    assert len(detections) == 20


def test_missing_json_raises():
    with pytest.raises(DetectorError):
        parse_items("Извините, я не могу разобрать это изображение.")


def test_malformed_json_raises():
    with pytest.raises(DetectorError):
        parse_items('{"items": [{"name": "молоко"}')
