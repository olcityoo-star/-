# Умный холодильник

Веб‑сервер для камеры **GoPlus CamPro / ActionCam_f8160c0282c2**: снимок полок, YOLO, OCR этикеток и синхронизация инвентаря.

## Версия 0.2

- YOLO находит объекты на кадре
- OCR (RapidOCR) читает текст/срок с кропа этикетки
- Синхронизация полок: **осталось / новое / пропало**
- Память переименований («Бутылка» → «Молоко»)
- Список покупок по срокам годности

> На macOS с Python 3.14 нужен пакет `rapidocr` (не старый `rapidocr-onnxruntime`).

## Запуск

```bash
cd ~/smart-fridge
git pull origin cursor/smart-fridge-web-261c
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn fridge.main:app --host 0.0.0.0 --port 8000
```

Откройте [http://127.0.0.1:8000](http://127.0.0.1:8000).

Один раз нажмите **«Скачать YOLO»**.

## Камера

ActionCam поднимает свою Wi‑Fi сеть (`ActionCam_f8160c0282c2`). Подключите ноутбук к этой сети:

- хост: `192.168.25.1`
- поток: `http://192.168.25.1:8080/?action=stream`

Без камеры работает **«Загрузить фото»**.

## Тесты

```bash
pytest -q
```
