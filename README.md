# Умный холодильник

Веб‑сервер для камеры **GoPlus CamPro / ActionCam_f8160c0282c2**.

## Версия 0.3 — дообучение

1. YOLO находит объекты
2. OCR читает этикетку/срок
3. Синхронизация полок: осталось / новое / пропало
4. **Дообучение**: ваши правильные названия → датасет → свой классификатор
5. В следующий скан «Бутылка» может стать «Молоко Простоквашино»

Как копить данные:
- переименуйте детекции и нажмите **«Синхронизировать полки»** — кропы сохранятся сами
- или в блоке **«Дообучение»** загрузите фото упаковки с названием

## Запуск

```bash
cd ~/smart-fridge
git pull origin cursor/smart-fridge-web-261c
source .venv/bin/activate
pip install -r requirements.txt
uvicorn fridge.main:app --host 0.0.0.0 --port 8000
```

Откройте [http://127.0.0.1:8000](http://127.0.0.1:8000).

На Python 3.14 для OCR: пакет `rapidocr` (уже в `requirements.txt`).

## Камера GoPlus CamPro

SSID `ActionCam_f8160c0282c2`, gateway `192.168.100.1`.

У разных прошивок разные протоколы. Сначала диагностика:

```bash
cd ~/smart-fridge
git pull origin cursor/smart-fridge-web-261c
source .venv/bin/activate
python -m fridge.cam_diag 192.168.100.1
```

Если `TCP :6666` — **connection refused**, это нормально для CamPro: у вашей модели
нет libipcamera-порта 6666.

Если MJPEG пустой, включите **live preview в GoPlus CamPro на телефоне**
(Mac и телефон на Wi‑Fi камеры) и сразу снова:

```bash
python -m fridge.cam_diag 192.168.100.1
curl -m 5 "http://192.168.100.1:8080/?action=stream" -o /tmp/cam.bin
```

Пока поток не найден — **«Загрузить фото»** в веб-интерфейсе.

## Тесты

```bash
pytest -q
```
