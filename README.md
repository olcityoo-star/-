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

**PCAP GoPlus CamPro:** видео идёт по **RTSP**, не по HTTP:

```text
rtsp://192.168.100.1:8080/?action=stream
```

Проверка на Mac (только ActionCam Wi‑Fi, телефон отключён):

```bash
# RTSP-метаданные (TCP достаточно):
ffprobe -rtsp_transport tcp -i "rtsp://192.168.100.1:8080/?action=stream"

# Кадр с камеры — видео идёт RTP/UDP (ffmpeg 9: `-timeout`, не `-stimeout`):
ffmpeg -rtsp_transport udp -timeout 10000000 -analyzeduration 10M -probesize 10M \
  -i "rtsp://192.168.100.1:8080/?action=stream" \
  -map 0:v:0 -frames:v 1 -update 1 -an -y /tmp/cam.jpg

python -m fridge.cam_diag 192.168.100.1
```

Если `ffmpeg … -rtsp_transport tcp` падает с `Failed reading RTSP data: End of file` —
это нормально для GoPlus: `ffprobe` видит поток, а кадр берите через **udp** или
**«Скан с камеры»** в UI (сервер пробует udp → tcp → свой RTSP-клиент).

Если `address already in use` на порту 8000:

```bash
lsof -i :8000
kill <PID>
uvicorn fridge.main:app --host 0.0.0.0 --port 8000
```

Или сразу другой порт: `--port 8001` → браузер `http://127.0.0.1:8001`.

4. В браузере на том же Mac: «Найти поток» → «Скан с камеры».

Для постоянной работы позже можно перевести камеру в режим **Station** (подключение к
домашнему Wi‑Fi через веб `http://192.168.100.1` или `http://192.168.25.1`) и держать
мини‑ПК как единственный клиент.

Пока поток не найден — **«Загрузить фото»**.

## Тесты

```bash
pytest -q
```
