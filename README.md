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

Камера Generalplus **спит**, пока приложение не «разбудит» её ICMP-пакетом с текстом
`99 bottles of beer on the wall`. После этого поток обычно доступен по HTTP MJPEG:

```bash
# поток = http://192.168.100.1:8080/?action=stream
# снимок = http://192.168.100.1:8080/?action=snapshot
```

Проверка вручную (Mac, подключены к Wi‑Fi камеры):

```bash
ping -c 3 192.168.100.1
curl -m 5 "http://192.168.100.1:8080/?action=stream" -o /tmp/cam.bin
xxd /tmp/cam.bin | head
```

Если `curl` всё ещё «Empty reply» — откройте GoPlus CamPro на телефоне (preview),
затем повторите `curl`. RTSP `404` без wakeup — нормально: PCAPdroid видит RTSP
только когда приложение уже разбудило камеру.

Сервер шлёт wakeup сам при «Найти поток» / «Проверить камеру». Если не помогает,
запустите с правами на ICMP:

```bash
sudo uvicorn fridge.main:app --host 0.0.0.0 --port 8000
```

Пока поток не найден, используйте **«Загрузить фото»**.

## Тесты

```bash
pytest -q
```
