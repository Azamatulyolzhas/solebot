# SoleBot — Инструкция по запуску

## Структура проекта
```
solebot/
├── main.py          ← главный файл сервера
├── sneakers.db      ← база данных склада (скопируйте из предыдущего шага)
├── .env             ← ваши секретные ключи (создать из .env.example)
├── requirements.txt ← зависимости Python
└── README.md        ← эта инструкция
```

---

## Шаг 1 — Установка

```bash
# Создать виртуальное окружение
python -m venv venv
source venv/bin/activate       # Linux/Mac
venv\Scripts\activate          # Windows

# Установить зависимости
pip install -r requirements.txt

# Скопировать .env.example → .env и заполнить ключи
cp .env.example .env
```

---

## Шаг 2 — Telegram бот (самый простой)

1. Напишите **@BotFather** в Telegram
2. Отправьте `/newbot` → придумайте имя → получите токен
3. Вставьте токен в `.env`:
   ```
   TELEGRAM_BOT_TOKEN=7123456789:AAF...
   ```
4. Для webhook нужен HTTPS-сервер (см. Шаг 5)

---

## Шаг 3 — WhatsApp Business API

1. Зайдите на [developers.facebook.com](https://developers.facebook.com)
2. Создайте приложение → выберите **WhatsApp**
3. В разделе **API Setup** найдите:
   - `Access Token` → `WHATSAPP_TOKEN`
   - `Phone Number ID` → `WHATSAPP_PHONE_NUMBER_ID`
4. В разделе **Webhooks** укажите:
   - URL: `https://yourdomain.com/wa/webhook`
   - Verify token: любое слово из вашего `.env`

> ⚠️ WhatsApp Business API требует одобрения Meta — займёт 1-3 дня.
> Для теста сразу доступен тестовый номер.

---

## Шаг 4 — Instagram

1. На той же странице [developers.facebook.com](https://developers.facebook.com)
2. Добавьте продукт **Instagram** → **Messenger API**
3. Подключите Instagram Business аккаунт
4. Webhook URL: `https://yourdomain.com/ig/webhook`

---

## Шаг 5 — Деплой (бесплатные варианты)

### Railway (рекомендую для старта)
```bash
# 1. Установите Railway CLI
npm install -g @railway/cli

# 2. Войдите и задеплойте
railway login
railway init
railway up

# 3. Получите URL и обновите TELEGRAM_WEBHOOK_URL в .env
```

### Render.com
1. Зарегистрируйтесь на [render.com](https://render.com)
2. New → Web Service → подключите GitHub репозиторий
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Добавьте переменные окружения в раздел Environment

### VPS (самый надёжный)
```bash
# На сервере Ubuntu:
git clone ваш-репозиторий
cd solebot
pip install -r requirements.txt

# Запуск через systemd или:
uvicorn main:app --host 0.0.0.0 --port 8000

# Nginx как reverse proxy + Let's Encrypt для HTTPS
```

---

## Локальный тест без HTTPS

Для разработки используйте **ngrok** — он создаёт временный HTTPS туннель:

```bash
# Установить ngrok: https://ngrok.com
ngrok http 8000

# Вы получите URL типа: https://abc123.ngrok.io
# Используйте его как TELEGRAM_WEBHOOK_URL
```

---

## Проверка работы

```bash
# Запустить сервер
python main.py

# Проверить статус (в другом терминале)
curl http://localhost:8000/
# → {"status":"ok","sneakers_in_db":63,"channels":{...}}

# Тест веб-чата
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Есть ли Nike Air Force 1 в размере 42?", "session_id": "test"}'
```

---

## Добавить новые кроссовки в базу

```python
import sqlite3
conn = sqlite3.connect("sneakers.db")
conn.execute("""
    INSERT INTO sneakers (brand, model, colorway, size, quantity, price, category, gender)
    VALUES ('Nike', 'Air Max 95', 'Neon', 42.0, 3, 78000, 'lifestyle', 'unisex')
""")
conn.commit()
conn.close()
```

---

## Поддержка

- Telegram: бот отвечает сразу после деплоя
- WhatsApp: нужно одобрение Meta (~1-3 дня)
- Instagram: нужен Business аккаунт
- Web: файл `sneaker_bot.html` работает сразу через браузер
