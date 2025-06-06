import telebot
import requests
import sqlite3
import threading
import time
from datetime import datetime, timedelta

# Конфигурация
API_TOKEN = 'BLABLABLABLABLABLABLABLABLABL'
CHECK_INTERVAL = 300  # Проверять каждые 5 минут (в секундах)

bot = telebot.TeleBot(API_TOKEN)


# База данных для хранения chat_id и подписок
def init_db():
    conn = sqlite3.connect('users.db', check_same_thread=False)
    cursor = conn.cursor()

    # Сначала создаем таблицу, если её нет
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (chat_id INTEGER PRIMARY KEY,
                       last_notified_status TEXT)''')

    # Затем проверяем наличие колонки is_subscribed
    cursor.execute("PRAGMA table_info(users)")
    columns = [column[1] for column in cursor.fetchall()]

    if 'is_subscribed' not in columns:
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN is_subscribed BOOLEAN DEFAULT FALSE")
        except sqlite3.OperationalError as e:
            print(f"Ошибка при добавлении колонки: {e}")

    conn.commit()
    return conn, cursor


db_conn, db_cursor = init_db()


# Функции для работы с временем
def convert_utc_to_ekb(utc_time_str):
    utc_time = datetime.strptime(utc_time_str, "%Y-%m-%d %H:%M:%S.%f%z")
    ekb_time = utc_time + timedelta(hours=5)
    return ekb_time.strftime("%d.%m.%Y %H:%M:%S")


# Получение текущего статуса релиза
def get_release_status(auth_token):
    try:
        res = requests.get(
            url='https://api.raveon.net/catalogue/v2/get/albums/my',
            headers={'Authorization': auth_token}
        )
        el = res.json()
        album = el["data"]['albums'][0]['album_serialized']
        return {
            'title': album['title'],
            'current_status': album['status']["current"],
            'history': album['status']["history"]
        }
    except Exception as e:
        print(f"Ошибка при получении статуса: {e}")
        return None


# Авторизация
def authenticate():
    url = "https://api.raveon.net/auth/user/get/token"
    form_data = {"email": "HAHAHAHAHAHAHAHAHAHAHAH", "password": "AHAHAHAHAHAHAHAHAHAH"}
    response = requests.post(url, data=form_data)
    if response.status_code == 201:
        return response.json()['data']['serialized_session']['token']
    raise Exception('Authentication failed')


# Отправка уведомлений подписанным пользователям
def notify_users(new_status, title):
    db_cursor.execute("SELECT chat_id FROM users WHERE is_subscribed = TRUE")
    for (chat_id,) in db_cursor.fetchall():
        try:
            bot.send_message(
                chat_id,
                f"🔔 Изменение статуса релиза!\n\n"
                f"Релиз: {title}\n"
                f"Новый статус: {new_status}"
            )
            db_cursor.execute(
                "UPDATE users SET last_notified_status = ? WHERE chat_id = ?",
                (new_status, chat_id)
            )
            db_conn.commit()
        except Exception as e:
            print(f"Не удалось отправить уведомление {chat_id}: {e}")


# Фоновая проверка статуса
def check_status_periodically():
    last_status = None
    while True:
        try:
            auth_token = authenticate()
            status_data = get_release_status(auth_token)

            if status_data and (last_status and status_data['current_status'] != last_status):
                notify_users(status_data['current_status'], status_data['title'])
                last_status = status_data['current_status']

        except Exception as e:
            print(f"Ошибка в фоновой проверке: {e}")

        time.sleep(CHECK_INTERVAL)


# Запуск фоновой проверки
threading.Thread(target=check_status_periodically, daemon=True).start()


# Команда /start (без автоматической подписки)
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn_check = telebot.types.KeyboardButton('Проверить статус')
    btn_subscribe = telebot.types.KeyboardButton('Управление подпиской')
    markup.add(btn_check, btn_subscribe)

    # Добавляем пользователя в БД, если его нет (с is_subscribed=FALSE)
    db_cursor.execute(
        "INSERT OR IGNORE INTO users (chat_id, is_subscribed) VALUES (?, FALSE)",
        (message.chat.id,)
    )
    db_conn.commit()

    bot.send_message(
        message.chat.id,
        "Выберите действие:",
        reply_markup=markup
    )


# Обработчик кнопки "Управление подпиской"
@bot.message_handler(func=lambda message: message.text == 'Управление подпиской')
def manage_subscription(message):
    db_cursor.execute(
        "SELECT is_subscribed FROM users WHERE chat_id = ?",
        (message.chat.id,)
    )
    subscription_status = db_cursor.fetchone()

    if subscription_status is None:
        bot.send_message(message.chat.id, "Сначала нажмите /start")
        return

    is_subscribed = subscription_status[0]

    markup = telebot.types.InlineKeyboardMarkup()
    if is_subscribed:
        markup.add(telebot.types.InlineKeyboardButton(
            "❌ Отписаться от рассылки",
            callback_data="unsubscribe")
        )
        text = "Вы подписаны на уведомления.\nХотите отписаться?"
    else:
        markup.add(telebot.types.InlineKeyboardButton(
            "✅ Подписаться на рассылку",
            callback_data="subscribe")
        )
        text = "Вы не подписаны на уведомления.\nХотите подписаться?"

    bot.send_message(message.chat.id, text, reply_markup=markup)


# Обработчик inline-кнопок подписки/отписки
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == "subscribe":
        db_cursor.execute(
            "UPDATE users SET is_subscribed = TRUE WHERE chat_id = ?",
            (call.message.chat.id,)
        )
        db_conn.commit()
        bot.answer_callback_query(call.id, "✅ Вы подписались на уведомления!")
    elif call.data == "unsubscribe":
        db_cursor.execute(
            "UPDATE users SET is_subscribed = FALSE WHERE chat_id = ?",
            (call.message.chat.id,)
        )
        db_conn.commit()
        bot.answer_callback_query(call.id, "❌ Вы отписались от уведомлений.")

    # Удаляем inline-клавиатуру после нажатия
    bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=None
    )


# Ручная проверка статуса
@bot.message_handler(func=lambda message: message.text == 'Проверить статус')
def manual_check(message):
    try:
        auth_token = authenticate()
        status_data = get_release_status(auth_token)

        if status_data:
            history = "\n".join(
                f"[{convert_utc_to_ekb(s['added_at'])}] Статус: {s['status']}"
                for s in status_data['history']
            )

            bot.send_message(
                message.chat.id,
                f"Релиз: {status_data['title']}\n\n"
                f"Текущий статус: {status_data['current_status']}\n\n"
                f"История:\n{history}"
            )
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при получении статуса: {e}")


# Запуск бота
if __name__ == "__main__":
    print("Бот запущен!")

    db_cursor.execute("SELECT chat_id FROM users WHERE is_subscribed = TRUE")
    for (chat_id,) in db_cursor.fetchall():
        try:
            bot.send_message(chat_id, "Бот был перезапущен.")
        except:
            print('Уведомление не сработало')

    bot.polling()
