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

**Обычный `ping` не включает preview.** GoPlus CamPro сначала логинится по **TCP :6666**
(`admin` / `12345`), шлёт команду «start preview», и только потом открывается
HTTP MJPEG на `:8080`.

Проверка вручную (Mac, Wi‑Fi камеры):

```bash
git pull origin cursor/smart-fridge-web-261c
pip install -r requirements.txt

# 1) открыт ли control-порт?
nc -zv 192.168.100.1 6666

# 2) запустить preview (наш скрипт)
python -m fridge.goplus 192.168.100.1

# 3) пока скрипт держит сессию (~5 сек), в другом окне:
curl -m 5 "http://192.168.100.1:8080/?action=stream" -o /tmp/cam.bin
xxd /tmp/cam.bin | head
```

Если `nc` к :6666 не коннектится — закройте GoPlus CamPro на телефоне (камера
может быть занята). Если preview стартует, но `curl` пустой — пришлите вывод
`nc -zv 192.168.100.1 6666` и `python -m fridge.goplus 192.168.100.1`.

В веб-интерфейсе **«Найти поток»** делает то же самое автоматически.

Пока поток не найден, используйте **«Загрузить фото»**.

## Тесты

```bash
pytest -q
```
