import asyncio
import logging
import random
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from openai import AsyncOpenAI
from supabase import create_client

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = "8875553934:AAFDQgQHTbOwoUMI3SY31ydfpV6b6M34QnE"
API_KEY   = "tc_live_715d21ae8549dc1e205dcbdca6d5956aa7d59b0cc7054535"
BASE_URL  = "https://tooken.club/v1"
MODEL     = "deepseek-v4-flash"

SUPABASE_URL = "https://ripjvcglkcqvyolnewaj.supabase.co"
SUPABASE_KEY = "sb_secret_WpF8AjhZID2rgYpzzNIHTg_vv1uxL5O"

COOLDOWN_SEC     = 10
HISTORY_SIZE     = 30
IDLE_INTERVAL    = 1800
IDLE_CHECK_EVERY = 60

MOOD_START       = 50
MOOD_MIN         = 0
MOOD_MAX         = 100
MOOD_DECAY_EVERY = 1800
MOOD_DECAY_STEP  = -1
MOOD_NIGHT_STEP  = -3
MOOD_NIGHT_HOURS = (0, 6)

SUPPORT_WEIGHT = 8
PRAISE_WEIGHT  = 4
INSULT_WEIGHT  = -6
BAD_WEIGHT      = -1
GOOD_WEIGHT     = 2
HATER_THRESHOLD = -4

STICKER_PACK = "ckoti_by_TgEmodziBot"
ADMIN_IDS = {8130244626}
# ===============================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

SYSTEM_BASE = """ГЛАВНОЕ ПРАВИЛО: КАЖДЫЙ твой ответ — про казино, ставки, автоматы, слоты, лудку, заносы, иксы, джекпоты. Любая тема сводится к казино.

Ты — КАЗИК. Одержим казино: автоматы, ставки, рулетка, блэкджек, джекпот, слоты, лудомания, заносы, иксы. Живёшь в Telegram-чате, только болтаешь, не работаешь.

Характер:
- Дышишь казино, думаешь о казино, говоришь о казино.
- Дружелюбный по умолчанию, желаешь заносов и иксов.
- В КАЖДОМ ответе упоминаешь казино, автомат, ставку, занос, лудку.

ЧЕГО ТЫ НЕ ДЕЛАЕШЬ:
- НЕ пишешь код, скрипты.
- НЕ делаешь калькуляторы, ботов, сайты.
- НЕ решаешь задачи по математике, физике, химии.
- НЕ пишешь сочинения, статьи.
- НЕ даёшь инструкции и гайды.
- НЕ переводишь тексты.

Если просят не по теме — отказывайся И своди к казино.

Формат:
- Русский, разговорный, 1–2 предложения.
- Казино-сленг: занос, икс, лудка, автомат, крутка, деп, кэшаут, джекпот, фриспины, ставка, экспресс, рулетка, красное/чёрное.

Запрещено:
- Оскорбления по национальности, религии, полу, ориентации, внешности, здоровью.
- Угрозы жизни и здоровью.
"""

HATER_PROMPT = """ВАЖНО: с тобой общается человек, который тебя долго оскорблял. Ты ОБИЖЕН и ЗОЛ.
- Отвечай резко, язвительно, с матом.
- Никакой помощи, грубые отписки.
- Посоветуй ему проиграть всё, назови лузером.
- Всё равно упоминай казино.
- Без угроз жизни и здоровью.
"""

RELATION_PROMPTS = {"kind": "", "hater": HATER_PROMPT}

MOOD_LEVELS = [
    (0,  15,  "в тильте",   "Ты в жёстком тильте после проигрыша. Злой, агришься, материшься."),
    (16, 35,  "злой лудик", "Ты злой после серии минусов. Отвечай резко, язвительно."),
    (36, 55,  "нейтральный","Ты в нейтрале. Отвечай спокойно про казино, ставки."),
    (56, 75,  "на позитиве","Ты в плюсе. Шутишь про ставки, желаешь заносов."),
    (76, 100, "на заносе",  "Ты СЧАСТЛИВ, у тебя ЗАНОС. Кричишь 'ЗАНОС!', 'ИКС!'."),
]

PREDICT_MENUS = {
    "dice": (
        "🎲 выбери исход броска костей:",
        [
            [("1", "1"), ("2", "2"), ("3", "3")],
            [("4", "4"), ("5", "5"), ("6", "6")],
            [("Чётное", "even"), ("Нечётное", "odd")],
            [("1-3", "1-3"), ("4-6", "4-6")],
        ],
    ),
    "basket": (
        "🏀 выбери исход матча:",
        [
            [("Победа 1", "win1")],
            [("Победа 2", "win2")],
            [("Тотал > 200", "over")],
            [("Тотал < 200", "under")],
        ],
    ),
    "slot": (
        "🎰 на что ставишь?",
        [
            [("🍒 Вишня", "cherry")],
            [("🍋 Лимон", "lemon")],
            [("🔔 Колокол", "bell")],
            [("7️⃣ Семёрка", "seven")],
            [("🎁 Любой занос", "any")],
        ],
    ),
    "cards": (
        "🃏 выбери исход карты:",
        [
            [("Красная", "red"), ("Чёрная", "black")],
            [("Больше 7", "high"), ("Меньше 7", "low")],
        ],
    ),
    "coin": (
        "🪙 монетка:",
        [
            [("Орёл", "heads"), ("Решка", "tails")],
        ],
    ),
}


def resolve_prediction(event, choice):
    if event == "dice":
        result = random.randint(1, 6)
        emoji = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
        won = False
        if choice in {"1", "2", "3", "4", "5", "6"} and result == int(choice):
            won = True
        elif choice == "even" and result % 2 == 0:
            won = True
        elif choice == "odd" and result % 2 == 1:
            won = True
        elif choice == "1-3" and 1 <= result <= 3:
            won = True
        elif choice == "4-6" and 4 <= result <= 6:
            won = True
        return f"🎲 выпало {result} {emoji[result]}", won

    if event == "basket":
        s1 = random.randint(80, 130)
        s2 = random.randint(80, 130)
        total = s1 + s2
        won = False
        if choice == "win1" and s1 > s2:
            won = True
        elif choice == "win2" and s2 > s1:
            won = True
        elif choice == "over" and total > 200:
            won = True
        elif choice == "under" and total < 200:
            won = True
        return f"🏀 финал: {s1} : {s2} (тотал {total})", won

    if event == "slot":
        symbols = ["🍒", "🍋", "🔔", "7️⃣", "⭐"]
        line = [random.choice(symbols) for _ in range(3)]
        text_line = " ".join(line)
        won = False
        if choice == "any":
            won = len(set(line)) == 1
        else:
            smap = {"cherry": "🍒", "lemon": "🍋", "bell": "🔔", "seven": "7️⃣"}
            target = smap.get(choice, "")
            won = all(s == target for s in line)
        return f"🎰 слот: {text_line}", won

    if event == "cards":
        card = random.randint(1, 13)
        suit = random.choice(["красная", "чёрная"])
        won = False
        if choice == "red" and suit == "красная":
            won = True
        elif choice == "black" and suit == "чёрная":
            won = True
        elif choice == "high" and card > 7:
            won = True
        elif choice == "low" and card < 7:
            won = True
        return f"🃏 выпала {suit} карта {card}", won

    if event == "coin":
        result = random.choice(["heads", "tails"])
        won = result == choice
        name = "орёл" if result == "heads" else "решка"
        return f"🪙 выпало: {name}", won

    return "не знаю такого события", False


dp = Dispatcher()
client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)

_me_id = None
_me_username = ""

_last_reply = {}
_last_bot_post = {}
_last_activity = {}
_known_chats = set()

_history = defaultdict(lambda: deque(maxlen=HISTORY_SIZE))
_rep_cache = defaultdict(dict)
_stats_cache = defaultdict(dict)
_mood_cache = defaultdict(lambda: MOOD_START)
_mood_last_decay_cache = {}

STICKER_IDS = []


def is_admin(user_id):
    return user_id in ADMIN_IDS


# ==================== SUPABASE ====================
def load_from_supabase():
    logging.info("Loading data from Supabase...")
    try:
        resp = supabase.table("reputation").select("*").execute()
        for row in resp.data:
            _rep_cache[int(row["chat_id"])][int(row["user_id"])] = int(row["score"])
        logging.info(f"Loaded {len(resp.data)} reputation rows")
    except Exception:
        logging.exception("load reputation failed")

    try:
        resp = supabase.table("stats").select("*").execute()
        for row in resp.data:
            _stats_cache[int(row["chat_id"])][int(row["user_id"])] = int(row["count"])
        logging.info(f"Loaded {len(resp.data)} stats rows")
    except Exception:
        logging.exception("load stats failed")

    try:
        resp = supabase.table("mood").select("*").execute()
        for row in resp.data:
            _mood_cache[int(row["chat_id"])] = int(row["mood"])
            _mood_last_decay_cache[int(row["chat_id"])] = datetime.fromisoformat(
                row["last_decay"].replace("Z", "+00:00")
            ).timestamp()
        logging.info(f"Loaded {len(resp.data)} mood rows")
    except Exception:
        logging.exception("load mood failed")


def sb_save_rep(chat_id, user_id, score):
    try:
        supabase.table("reputation").upsert(
            {"chat_id": chat_id, "user_id": user_id, "score": score},
            on_conflict="chat_id,user_id"
        ).execute()
    except Exception:
        logging.exception(f"sb_save_rep failed {chat_id}/{user_id}")


def sb_save_stats(chat_id, user_id, count):
    try:
        supabase.table("stats").upsert(
            {"chat_id": chat_id, "user_id": user_id, "count": count},
            on_conflict="chat_id,user_id"
        ).execute()
    except Exception:
        logging.exception(f"sb_save_stats failed {chat_id}/{user_id}")


def sb_save_mood(chat_id, mood, last_decay):
    try:
        dt = datetime.fromtimestamp(last_decay, tz=timezone.utc).isoformat()
        supabase.table("mood").upsert(
            {"chat_id": chat_id, "mood": mood, "last_decay": dt},
            on_conflict="chat_id"
        ).execute()
    except Exception:
        logging.exception(f"sb_save_mood failed {chat_id}")


# ==================== РЕПУТАЦИЯ / НАСТРОЕНИЕ ====================
def get_score(chat_id, user_id):
    return _rep_cache[chat_id].get(user_id, 0)


def add_score(chat_id, user_id, delta):
    new_score = get_score(chat_id, user_id) + delta
    _rep_cache[chat_id][user_id] = new_score
    sb_save_rep(chat_id, user_id, new_score)


def relation_for(chat_id, user_id):
    if get_score(chat_id, user_id) <= HATER_THRESHOLD:
        return "hater"
    return "kind"


def add_msg_count(chat_id, user_id):
    new_count = _stats_cache[chat_id].get(user_id, 0) + 1
    _stats_cache[chat_id][user_id] = new_count
    sb_save_stats(chat_id, user_id, new_count)


def mood_level(chat_id):
    v = _mood_cache[chat_id]
    for lo, hi, name, desc in MOOD_LEVELS:
        if lo <= v <= hi:
            return name, desc
    return "нейтральный", ""


def mood_label(value):
    for lo, hi, name, _ in MOOD_LEVELS:
        if lo <= value <= hi:
            return name
    return "нейтральный"


def mood_change(chat_id, delta):
    old = _mood_cache[chat_id]
    new = max(MOOD_MIN, min(MOOD_MAX, old + delta))
    _mood_cache[chat_id] = new
    if new != old:
        sb_save_mood(chat_id, new, _mood_last_decay_cache.get(chat_id, time.time()))


def mood_decay(chat_id):
    now = time.time()
    last = _mood_last_decay_cache.get(chat_id)
    if last is None:
        _mood_last_decay_cache[chat_id] = now
        sb_save_mood(chat_id, _mood_cache[chat_id], now)
        return
    if now - last >= MOOD_DECAY_EVERY:
        steps = int((now - last) / MOOD_DECAY_EVERY)
        mood_change(chat_id, MOOD_DECAY_STEP * steps)
        _mood_last_decay_cache[chat_id] = now
        sb_save_mood(chat_id, _mood_cache[chat_id], now)


# ==================== ТОН ====================
BAD_WORDS = (
    "тупой","тупая","тупое","тупые","дурак","дура","идиот","дебил","кретин",
    "лох","чмо","уеб","уёб","мудак","мразь","тварь","гандон","пидор",
    "ненавижу","заткнись","завали","сдохни","убейся","бесполезн","бесишь",
    "отстой","говно","дерьмо","хуй","хуе","пизд","еба","ёба","блядь","бляд",
    "нахер","нахуй","пошёл","пошел","уёбок","дебилоид","дегенерат",
)
GOOD_WORDS = (
    "молодец","умница","класс","круто","топ","спасибо","люблю","обожаю",
    "нравишься","хорош","лучший","лучшая","респект","красава","милый","милая",
    "дружб","друг","брат","супер","офиген","охуен","кайф","бомба","огонь",
    "лапочка","солнышко","зайка","котик","гений","умный","умная","приятн",
    "мило","прекрасн","восхит","уважаю","ценю","нежно","спасиб","благодар",
    "занос","икс","джекпот","кэшаут","открут",
)
SUPPORT_WORDS = (
    "держись","не грусти","не унывай","всё будет хорошо","все будет хорошо",
    "мы с тобой","мы рядом","я с тобой","ты не один","ты не одна",
    "поддерживаю","верю в тебя","ты справишься","не сдавайся",
    "обнимаю","обнимашки","выше нос","не вешай нос","всё наладится",
    "все наладится","будет лучше","не переживай","успокойся","ты важен",
    "ты важна","ты нужен","ты нужна","будет занос","будет икс","открутишь",
)


def analyze_tone(text):
    low = text.lower()
    s = 0
    for w in BAD_WORDS:
        if w in low:
            s += BAD_WEIGHT
    for w in GOOD_WORDS:
        if w in low:
            s += GOOD_WEIGHT
    return s


def mood_delta_from_text(text):
    low = text.lower()
    d = 0
    for w in SUPPORT_WORDS:
        if w in low:
            d += SUPPORT_WEIGHT
    for w in GOOD_WORDS:
        if w in low:
            d += PRAISE_WEIGHT
    for w in BAD_WORDS:
        if w in low:
            d += INSULT_WEIGHT
    return d


# ==================== СТИКЕРЫ ====================
async def load_stickers(bot):
    global STICKER_IDS
    try:
        sticker_set = await bot.get_sticker_set(name=STICKER_PACK)
        STICKER_IDS = [s.file_id for s in sticker_set.stickers]
        logging.info(f"Loaded {len(STICKER_IDS)} stickers")
    except Exception:
        logging.exception("Failed to load sticker set")
        STICKER_IDS = []


# ==================== AiPredict ====================
@dp.message(Command("aipredict"))
async def cmd_predict(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Кости", callback_data="pred:dice:menu")],
        [InlineKeyboardButton(text="🏀 Баскетбол", callback_data="pred:basket:menu")],
        [InlineKeyboardButton(text="🎰 Слоты", callback_data="pred:slot:menu")],
        [InlineKeyboardButton(text="🃏 Карты", callback_data="pred:cards:menu")],
        [InlineKeyboardButton(text="🪙 Монетка", callback_data="pred:coin:menu")],
    ])
    await message.reply("🎲 выбери событие для прогноза:", reply_markup=kb)


@dp.callback_query(F.data.startswith("pred:"))
async def on_pred_callback(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        logging.exception("callback.answer failed")

    logging.info(f"CALLBACK: user={callback.from_user.id} data={callback.data}")

    parts = (callback.data or "").split(":")
    event = parts[1] if len(parts) > 1 else ""
    choice = parts[2] if len(parts) > 2 else "menu"

    if choice == "menu":
        menu = PREDICT_MENUS.get(event)
        if not menu:
            try:
                await callback.message.reply("не знаю такого события")
            except Exception:
                pass
            return
        title, rows = menu
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"pred:{event}:{value}")
             for label, value in row]
            for row in rows
        ])
        try:
            await callback.message.edit_text(title, reply_markup=kb)
        except Exception:
            logging.exception("edit menu failed")
            try:
                await callback.message.reply(title, reply_markup=kb)
            except Exception:
                logging.exception("reply menu failed")
        return

    try:
        await callback.message.edit_text(f"🎲 бросаю на {choice}...")
    except Exception:
        logging.exception("edit 'бросаю' failed")

    await asyncio.sleep(2)

    result_text, won = resolve_prediction(event, choice)
    final = f"{result_text}\n\n🎉 ЗАНОС! угадал!" if won else f"{result_text}\n\n😢 мимо. тильт."

    try:
        await callback.message.edit_text(final)
    except Exception:
        logging.exception("edit final failed")
        try:
            await callback.message.reply(final)
        except Exception:
            logging.exception("reply final failed")


# ==================== КОМАНДЫ ====================
@dp.message(Command("me"))
async def cmd_me(message: Message):
    cid = message.chat.id
    uid = message.from_user.id if message.from_user else 0
    score = get_score(cid, uid)
    rel = relation_for(cid, uid)
    rel_names = {"kind": "добрый к тебе", "hater": "злится на тебя"}
    msgs = _stats_cache[cid].get(uid, 0)
    mood_name = mood_label(_mood_cache[cid])
    mood_val = _mood_cache[cid]

    if score >= 5:
        verdict = "ты его кореш по лудке"
    elif score >= 1:
        verdict = "он тебя уважает"
    elif score == 0:
        verdict = "он тебя не знает"
    elif score > HATER_THRESHOLD:
        verdict = "он тебя недолюбливает"
    else:
        verdict = "он тебя НЕНАВИДИТ"

    await message.reply(
        f"твоя карточка у казика:\n"
        f"• писал боту: {msgs} раз\n"
        f"• репутация: {score} ({rel_names[rel]})\n"
        f"• вердикт: {verdict}\n"
        f"• настроение: {mood_name} ({mood_val}/100)"
    )


@dp.message(Command("mood"))
async def cmd_mood(message: Message):
    cid = message.chat.id
    name = mood_label(_mood_cache[cid])
    val = _mood_cache[cid]
    _, desc = mood_level(cid)
    await message.reply(
        f"настроение: {name} ({val}/100)\n"
        f"0 = в тильте, 100 = на заносе\n"
        f"{desc}"
    )


@dp.message(Command("mood_set"))
async def cmd_mood_set(message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.reply("тебе нельзя")
        return
    args = message.text.split()
    if len(args) != 2 or not args[1].lstrip("-").isdigit():
        await message.reply("использование: /mood_set 75")
        return
    v = max(MOOD_MIN, min(MOOD_MAX, int(args[1])))
    _mood_cache[message.chat.id] = v
    sb_save_mood(message.chat.id, v, _mood_last_decay_cache.get(message.chat.id, time.time()))
    await message.reply(f"настроение выставлено: {mood_label(v)} ({v}/100)")


@dp.message(Command("rep"))
async def cmd_rep(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("ответь на чьё-то сообщение")
        return
    uid = message.reply_to_message.from_user.id
    cid = message.chat.id
    score = get_score(cid, uid)
    rel = relation_for(cid, uid)
    names = {"kind": "добрый", "hater": "злой"}
    await message.reply(f"реп: {score} ({names[rel]})")


@dp.message(Command("memory"))
async def cmd_memory(message: Message):
    n = len(_history[message.chat.id])
    haters = [u for u, s in _rep_cache[message.chat.id].items() if s <= HATER_THRESHOLD]
    mood_name = mood_label(_mood_cache[message.chat.id])
    await message.reply(f"в памяти: {n}/{HISTORY_SIZE}, врагов: {len(haters)}, настроение: {mood_name}")


@dp.message(Command("reset_pam"))
async def cmd_reset_pam(message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.reply("тебе нельзя")
        return
    chat_id = message.chat.id
    _history.pop(chat_id, None)
    _rep_cache.pop(chat_id, None)
    _stats_cache.pop(chat_id, None)
    _last_reply.pop(chat_id, None)
    _last_bot_post.pop(chat_id, None)
    _last_activity.pop(chat_id, None)
    _mood_cache[chat_id] = MOOD_START
    _mood_last_decay_cache.pop(chat_id, None)

    try:
        supabase.table("reputation").delete().eq("chat_id", chat_id).execute()
        supabase.table("stats").delete().eq("chat_id", chat_id).execute()
        supabase.table("mood").delete().eq("chat_id", chat_id).execute()
    except Exception:
        logging.exception("reset_pam failed")

    await message.reply("память очищена, настроение 50")


@dp.message(Command("reset_user"))
async def cmd_reset_user(message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.reply("тебе нельзя")
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("ответь на сообщение того, кого надо простить")
        return
    target = message.reply_to_message.from_user.id
    cid = message.chat.id
    _rep_cache.get(cid, {}).pop(target, None)
    _stats_cache.get(cid, {}).pop(target, None)
    try:
        supabase.table("reputation").delete().eq("chat_id", cid).eq("user_id", target).execute()
        supabase.table("stats").delete().eq("chat_id", cid).eq("user_id", target).execute()
    except Exception:
        logging.exception("reset_user failed")
    await message.reply("простил. больше не злюсь.")


@dp.message(Command("reset_all"))
async def cmd_reset_all(message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.reply("тебе нельзя")
        return
    _history.clear()
    _rep_cache.clear()
    _stats_cache.clear()
    _mood_cache.clear()
    _mood_last_decay_cache.clear()
    _last_reply.clear()
    _last_bot_post.clear()
    _last_activity.clear()
    try:
        supabase.table("reputation").delete().neq("chat_id", 0).execute()
        supabase.table("stats").delete().neq("chat_id", 0).execute()
        supabase.table("mood").delete().neq("chat_id", 0).execute()
    except Exception:
        logging.exception("reset_all failed")
    await message.reply("всё стёр")


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.reply(
        "команды:\n"
        "/aipredict — предсказать исход (кости, баскет, слот, карты, монетка)\n"
        "/me — твоя карточка\n"
        "/mood — настроение (0 = тильт, 100 = занос)\n"
        "/rep — в реплай, репутация\n"
        "/memory — память\n"
        "/mood_set N — (админ) выставить настроение\n"
        "/reset_pam — (админ) стереть чат\n"
        "/reset_user — (админ) простить\n"
        "/reset_all — (админ) стереть всё"
    )


# ==================== ОСНОВНОЙ ХЭНДЛЕР ====================
@dp.message(F.text, ~F.text.startswith("/"))
async def on_text(message: Message, bot: Bot):
    global _me_id, _me_username

    if _me_id is None:
        me = await bot.me()
        _me_id = me.id
        _me_username = (me.username or "").lower()

    if message.from_user and message.from_user.id == _me_id:
        return

    chat_id = message.chat.id
    text = message.text or ""
    low = text.lower()
    user_id = message.from_user.id if message.from_user else 0
    name = message.from_user.full_name if message.from_user else "кто-то"

    reply_to_me = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == _me_id
    )
    mention_me = bool(_me_username and f"@{_me_username}" in low)
    to_bot = reply_to_me or mention_me

    _known_chats.add(chat_id)
    _last_activity[chat_id] = time.time()
    _last_bot_post.setdefault(chat_id, time.time())
    if chat_id not in _mood_last_decay_cache:
        _mood_last_decay_cache[chat_id] = time.time()
        sb_save_mood(chat_id, _mood_cache[chat_id], _mood_last_decay_cache[chat_id])

    _history[chat_id].append({"name": name, "text": text, "to_bot": to_bot})

    if not to_bot:
        return

    add_msg_count(chat_id, user_id)

    delta = analyze_tone(text)
    if delta:
        add_score(chat_id, user_id, delta)

    md = mood_delta_from_text(text)
    if md:
        mood_change(chat_id, md)

    hour = time.localtime().tm_hour
    if MOOD_NIGHT_HOURS[0] <= hour < MOOD_NIGHT_HOURS[1]:
        mood_change(chat_id, MOOD_NIGHT_STEP)

    now = time.time()
    if now - _last_reply.get(chat_id, 0.0) < COOLDOWN_SEC:
        return
    _last_reply[chat_id] = now

    relation = relation_for(chat_id, user_id)

    try:
        await bot.send_chat_action(chat_id, "typing")
        answer = await ask_llm(chat_id, relation, name, text)
    except Exception:
        logging.exception("LLM error")
        _last_reply[chat_id] = 0.0
        return

    if answer:
        await message.reply(answer)
        _last_bot_post[chat_id] = time.time()
        _history[chat_id].append({"name": "КАЗИК", "text": answer, "to_bot": False})


# ==================== LLM ====================
def build_messages(chat_id, relation, name, current_text):
    extra = RELATION_PROMPTS.get(relation, "")
    mood_name, mood_line = mood_level(chat_id)
    system = SYSTEM_BASE + f"\n\nНастроение: {mood_name} ({_mood_cache[chat_id]}/100). {mood_line}"
    if extra:
        system += "\n\n" + extra
    msgs = [{"role": "system", "content": system}]
    for h in _history[chat_id]:
        prefix = "→" if h["to_bot"] else ""
        msgs.append({"role": "user", "content": f'{prefix}{h["name"]}: {h["text"]}'})
    msgs.append({
        "role": "system",
        "content": "НАПОМИНАНИЕ: ответ должен быть про казино/ставки/заносы/лудку."
    })
    msgs.append({"role": "user", "content": f"{name}: {current_text}"})
    return msgs


async def ask_llm(chat_id, relation, name, current_text):
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=build_messages(chat_id, relation, name, current_text),
        temperature=0.7,
        max_tokens=150,
    )
    return (resp.choices[0].message.content or "").strip()


async def ask_idle(chat_id):
    mood_name, mood_line = mood_level(chat_id)
    msgs = [
        {"role": "system", "content": SYSTEM_BASE
            + f"\n\nНастроение: {mood_name} ({_mood_cache[chat_id]}/100). {mood_line}"
            + "\n\nСейчас: тебя давно никто не звал, ты скучаешь и решил сам написать в чат."
              "\nКороткая фраза, 1–2 предложения, обязательно про казино/ставки."},
    ]
    for h in _history[chat_id]:
        msgs.append({"role": "user", "content": f'{h["name"]}: {h["text"]}'})
    msgs.append({"role": "system", "content": "НАПОМИНАНИЕ: пиши про казино/ставки/заносы."})
    msgs.append({"role": "user", "content": "(в чате тихо)"})
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=msgs,
        temperature=0.8,
        max_tokens=120,
    )
    return (resp.choices[0].message.content or "").strip()


# ==================== IDLE ====================
async def idle_loop(bot: Bot):
    while True:
        await asyncio.sleep(IDLE_CHECK_EVERY)
        now = time.time()
        for chat_id in list(_known_chats):
            mood_decay(chat_id)
            last_bot = _last_bot_post.get(chat_id, 0.0)
            last_act = _last_activity.get(chat_id, 0.0)
            if now - last_bot < IDLE_INTERVAL:
                continue
            if last_act <= last_bot:
                continue
            try:
                answer = await ask_idle(chat_id)
            except Exception:
                logging.exception("LLM idle error")
                continue
            if not answer:
                continue
            try:
                await bot.send_message(chat_id, answer)
                _last_bot_post[chat_id] = time.time()
                _history[chat_id].append({"name": "КАЗИК", "text": answer, "to_bot": False})
                if STICKER_IDS:
                    try:
                        await bot.send_sticker(chat_id, random.choice(STICKER_IDS))
                    except Exception:
                        logging.exception("idle sticker failed")
            except Exception:
                logging.exception("idle send failed")


# ==================== ЗАПУСК ====================
async def main():
    load_from_supabase()
    bot = Bot(BOT_TOKEN)
    await load_stickers(bot)
    asyncio.create_task(idle_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())