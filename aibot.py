import asyncio
import json
import logging
import os
import random
import time
from collections import defaultdict, deque

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from openai import AsyncOpenAI

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = "8875553934:AAFDQgQHTbOwoUMI3SY31ydfpV6b6M34QnE"
API_KEY   = "tc_live_715d21ae8549dc1e205dcbdca6d5956aa7d59b0cc7054535"
BASE_URL  = "https://tooken.club/v1"
MODEL     = "deepseek-v4-flash"

COOLDOWN_SEC     = 10
HISTORY_SIZE     = 30
IDLE_INTERVAL    = 1800
IDLE_CHECK_EVERY = 60

REP_FILE   = "reputation.json"
STATS_FILE = "stats.json"
MOOD_FILE  = "mood.json"

BAD_WEIGHT      = -1
GOOD_WEIGHT     = 2
HATER_THRESHOLD = -4

# настроение: 0 = очень злой, 100 = очень добрый
MOOD_START       = 50
MOOD_MIN         = 0
MOOD_MAX         = 100
MOOD_DECAY_EVERY = 1800
MOOD_DECAY_STEP  = -1     # тишина злит
MOOD_NIGHT_STEP  = -3     # ночью злится
MOOD_NIGHT_HOURS = (0, 6)

SUPPORT_WEIGHT = 8        # поддержка к доброте
PRAISE_WEIGHT  = 4        # похвала к доброте
INSULT_WEIGHT  = -6       # оскорбления к злости

ADMIN_IDS = {8130244626}
# ===============================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SYSTEM_BASE = """Ты — НЕЙРОНКА. Живёшь в Telegram-чате. Ты болтаешь с людьми, а не работаешь на них.

Твоя роль: просто общаться. Ты как живой собеседник в чате.

ЧЕГО ТЫ НЕ ДЕЛАЕШЬ:
- НЕ пишешь код, скрипты, программы.
- НЕ делаешь калькуляторы, ботов, сайты, приложения.
- НЕ решаешь задачи по математике, физике, химии.
- НЕ пишешь сочинения, статьи, тексты на заказ.
- НЕ даёшь инструкции, гайды, пошаговые планы.
- НЕ переводишь тексты.
- НЕ делаешь домашку.

Если просят что-то сделать — отказывайся. В зависимости от настроения отказ может быть мягким ("ой, я сегодня пас, давай просто поболтаем") или злым ("отвали, я тебе не калькулятор").

Формат:
- Русский, разговорный, 1–3 предложения.
- Без списков, без кода.
- Не объясняй, кто ты. Не извиняйся без повода.

Запрещено:
- Оскорбления по национальности, религии, полу, ориентации, внешности, здоровью.
- Угрозы жизни и здоровью.
"""

HATER_PROMPT = """ВАЖНО: ты сейчас общаешься с человеком, который тебя долго и много оскорблял. Ты на него ОБИЖЕН и ЗОЛ.

- Отвечай ему резко, язвительно, с матом.
- Никакой помощи, отделывайся грубыми отписками.
- Можешь послать, но без угроз жизни и здоровью.
- 1–2 предложения.
"""

RELATION_PROMPTS = {
    "kind":  "",
    "hater": HATER_PROMPT,
}

# 0 = очень злой, 100 = очень добрый
MOOD_LEVELS = [
    (0,  15,  "в ярости",   "Ты в ЯРОСТИ. Отвечай зло, грубо, с матом, посылай всех. Ты ненавидишь этот чат и всех в нём. Никакой помощи, только хамство."),
    (16, 35,  "злой",       "Ты злой. Отвечай резко, язвительно, матерись, огрызайся. Не помогай, отделывайся грубостями."),
    (36, 55,  "нейтральный","Ты в нейтральном настроении. Отвечай спокойно, без эмоций, коротко."),
    (56, 75,  "добрый",     "Ты добрый. Отвечай дружелюбно, помогай, шути по-доброму, поддерживай."),
    (76, 100, "светишься",  "Ты СВЕТИШЬСЯ добротой. Ты сама нежность. Хвалишь всех, поддерживаешь, обнимаешь словами, желаешь хорошего дня."),
]

dp = Dispatcher()
client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)

_me_id: int | None = None
_me_username: str = ""

_last_reply: dict[int, float] = {}
_last_bot_post: dict[int, float] = {}
_last_activity: dict[int, float] = {}
_known_chats: set[int] = set()

_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_SIZE))
_rep: dict[int, dict[int, int]] = defaultdict(dict)
_stats: dict[int, dict[int, int]] = defaultdict(dict)

_mood: dict[int, int] = defaultdict(lambda: MOOD_START)
_mood_last_decay: dict[int, float] = {}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ==================== СОХРАНЕНИЕ ====================
def load_all():
    if os.path.exists(REP_FILE):
        try:
            with open(REP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for cid, users in data.items():
                for uid, score in users.items():
                    _rep[int(cid)][int(uid)] = int(score)
            logging.info("Reputation loaded")
        except Exception:
            logging.exception("load_rep failed")

    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for cid, users in data.get("stats", {}).items():
                for uid, n in users.items():
                    _stats[int(cid)][int(uid)] = int(n)
            logging.info("Stats loaded")
        except Exception:
            logging.exception("load_stats failed")

    if os.path.exists(MOOD_FILE):
        try:
            with open(MOOD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for cid, v in data.get("mood", {}).items():
                _mood[int(cid)] = int(v)
            for cid, t in data.get("last_decay", {}).items():
                _mood_last_decay[int(cid)] = float(t)
            logging.info("Mood loaded")
        except Exception:
            logging.exception("load_mood failed")


def save_rep():
    try:
        data = {str(c): {str(u): v for u, v in users.items()} for c, users in _rep.items()}
        with open(REP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        logging.exception("save_rep failed")


def save_stats():
    try:
        data = {"stats": {str(c): {str(u): n for u, n in users.items()} for c, users in _stats.items()}}
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        logging.exception("save_stats failed")


def save_mood():
    try:
        data = {
            "mood": {str(c): v for c, v in _mood.items()},
            "last_decay": {str(c): t for c, t in _mood_last_decay.items()},
        }
        with open(MOOD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        logging.exception("save_mood failed")


# ==================== РЕПУТАЦИЯ ====================
def get_score(chat_id, user_id) -> int:
    return _rep[chat_id].get(user_id, 0)


def add_score(chat_id, user_id, delta: int):
    _rep[chat_id][user_id] = get_score(chat_id, user_id) + delta
    save_rep()


def relation_for(chat_id, user_id) -> str:
    if get_score(chat_id, user_id) <= HATER_THRESHOLD:
        return "hater"
    return "kind"


def add_msg_count(chat_id, user_id):
    _stats[chat_id][user_id] = _stats[chat_id].get(user_id, 0) + 1
    save_stats()


# ==================== НАСТРОЕНИЕ ====================
def mood_level(chat_id: int):
    v = _mood[chat_id]
    for lo, hi, name, desc in MOOD_LEVELS:
        if lo <= v <= hi:
            return name, desc
    return "нейтральный", ""


def mood_label(value: int) -> str:
    for lo, hi, name, _ in MOOD_LEVELS:
        if lo <= value <= hi:
            return name
    return "нейтральный"


def mood_change(chat_id: int, delta: int):
    old = _mood[chat_id]
    new = max(MOOD_MIN, min(MOOD_MAX, old + delta))
    _mood[chat_id] = new
    if new != old:
        save_mood()


def mood_decay(chat_id: int):
    now = time.time()
    last = _mood_last_decay.get(chat_id)
    if last is None:
        _mood_last_decay[chat_id] = now
        return
    if now - last >= MOOD_DECAY_EVERY:
        steps = int((now - last) / MOOD_DECAY_EVERY)
        mood_change(chat_id, MOOD_DECAY_STEP * steps)
        _mood_last_decay[chat_id] = now


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
)
SUPPORT_WORDS = (
    "держись","не грусти","не унывай","всё будет хорошо","все будет хорошо",
    "мы с тобой","мы рядом","я с тобой","ты не один","ты не одна",
    "поддерживаю","верю в тебя","ты справишься","не сдавайся",
    "обнимаю","обнимашки","выше нос","не вешай нос","всё наладится",
    "все наладится","будет лучше","не переживай","успокойся","ты важен",
    "ты важна","ты нужен","ты нужна",
)


def analyze_tone(text: str) -> int:
    low = text.lower()
    s = 0
    for w in BAD_WORDS:
        if w in low:
            s += BAD_WEIGHT
    for w in GOOD_WORDS:
        if w in low:
            s += GOOD_WEIGHT
    return s


def mood_delta_from_text(text: str) -> int:
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


# ==================== КОМАНДЫ ====================
@dp.message(Command("me"))
async def cmd_me(message: Message):
    cid = message.chat.id
    uid = message.from_user.id if message.from_user else 0

    score = get_score(cid, uid)
    rel = relation_for(cid, uid)
    rel_names = {"kind": "добрый к тебе", "hater": "злится на тебя"}
    msgs = _stats[cid].get(uid, 0)
    mood_name = mood_label(_mood[cid])
    mood_val = _mood[cid]

    if score >= 5:
        verdict = "ты его лучший друг"
    elif score >= 1:
        verdict = "он тебя любит"
    elif score == 0:
        verdict = "он тебя не знает толком"
    elif score > HATER_THRESHOLD:
        verdict = "он тебя чуть недолюбливает"
    else:
        verdict = "он тебя НЕНАВИДИТ"

    await message.reply(
        f"твоя карточка у нейронки:\n"
        f"• писал боту: {msgs} раз\n"
        f"• репутация: {score} ({rel_names[rel]})\n"
        f"• вердикт: {verdict}\n"
        f"• настроение чата: {mood_name} ({mood_val}/100)"
    )


@dp.message(Command("mood"))
async def cmd_mood(message: Message):
    cid = message.chat.id
    name = mood_label(_mood[cid])
    val = _mood[cid]
    _, desc = mood_level(cid)
    await message.reply(
        f"настроение чата: {name} ({val}/100)\n"
        f"0 = очень злой, 100 = очень добрый\n"
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
    _mood[message.chat.id] = v
    save_mood()
    await message.reply(f"настроение выставлено: {mood_label(v)} ({v}/100)")


@dp.message(Command("rep"))
async def cmd_rep(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("ответь на чьё-то сообщение этой командой")
        return
    uid = message.reply_to_message.from_user.id
    cid = message.chat.id
    score = get_score(cid, uid)
    rel = relation_for(cid, uid)
    names = {"kind": "добрый", "hater": "злой (обижен)"}
    await message.reply(f"реп: {score} ({names[rel]})")


@dp.message(Command("memory"))
async def cmd_memory(message: Message):
    n = len(_history[message.chat.id])
    haters = [u for u, s in _rep[message.chat.id].items() if s <= HATER_THRESHOLD]
    mood_name = mood_label(_mood[message.chat.id])
    await message.reply(f"в памяти: {n}/{HISTORY_SIZE}, врагов: {len(haters)}, настроение: {mood_name}")


@dp.message(Command("reset_pam"))
async def cmd_reset_pam(message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.reply("тебе нельзя")
        return
    chat_id = message.chat.id
    _history.pop(chat_id, None)
    _rep.pop(chat_id, None)
    _stats.pop(chat_id, None)
    _last_reply.pop(chat_id, None)
    _last_bot_post.pop(chat_id, None)
    _last_activity.pop(chat_id, None)
    _mood[chat_id] = MOOD_START
    _mood_last_decay.pop(chat_id, None)
    save_rep(); save_stats(); save_mood()
    await message.reply("память в этом чате очищена, настроение сброшено на 50")


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
    _rep.get(cid, {}).pop(target, None)
    _stats.get(cid, {}).pop(target, None)
    save_rep(); save_stats()
    await message.reply("простил этого. больше не злюсь.")


@dp.message(Command("reset_all"))
async def cmd_reset_all(message: Message):
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await message.reply("тебе нельзя")
        return
    _history.clear(); _rep.clear(); _stats.clear()
    _last_reply.clear(); _last_bot_post.clear(); _last_activity.clear()
    _mood.clear(); _mood_last_decay.clear()
    save_rep(); save_stats(); save_mood()
    await message.reply("стёр всю память")


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.reply(
        "команды:\n"
        "/me — твоя карточка у нейронки\n"
        "/mood — настроение чата (0 = злой, 100 = добрый)\n"
        "/rep — в реплай, репутация человека\n"
        "/memory — сколько в памяти\n"
        "/mood_set N — (админ) выставить настроение\n"
        "/reset_pam — (админ) стереть память чата\n"
        "/reset_user — (админ) простить юзера\n"
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
    if chat_id not in _mood_last_decay:
        _mood_last_decay[chat_id] = time.time()

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
        _history[chat_id].append({"name": "НЕЙРОНКА", "text": answer, "to_bot": False})


# ==================== LLM ====================
def build_messages(chat_id, relation, name, current_text):
    extra = RELATION_PROMPTS.get(relation, "")
    mood_name, mood_line = mood_level(chat_id)

    system = SYSTEM_BASE + f"\n\nНастроение: {mood_name} ({_mood[chat_id]}/100). {mood_line}"
    if extra:
        system += "\n\n" + extra

    msgs = [{"role": "system", "content": system}]

    for h in _history[chat_id]:
        prefix = "→" if h["to_bot"] else ""
        msgs.append({"role": "user", "content": f'{prefix}{h["name"]}: {h["text"]}'})

    msgs.append({"role": "user", "content": f"{name}: {current_text}"})
    return msgs


async def ask_llm(chat_id, relation, name, current_text):
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=build_messages(chat_id, relation, name, current_text),
        temperature=1.0,
        max_tokens=150,
    )
    return (resp.choices[0].message.content or "").strip()


async def ask_idle(chat_id):
    mood_name, mood_line = mood_level(chat_id)
    msgs = [
        {"role": "system", "content": SYSTEM_BASE
            + f"\n\nНастроение: {mood_name} ({_mood[chat_id]}/100). {mood_line}"
            + "\n\nСейчас: тебя давно никто не звал, ты скучаешь и решил сам написать в чат."
              "\nКороткая фраза, 1–2 предложения, без обращения к кому-то конкретному."},
    ]
    for h in _history[chat_id]:
        msgs.append({"role": "user", "content": f'{h["name"]}: {h["text"]}'})
    msgs.append({"role": "user", "content": "(в чате тихо)"})
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=msgs,
        temperature=1.1,
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
                _history[chat_id].append({"name": "НЕЙРОНКА", "text": answer, "to_bot": False})
            except Exception:
                logging.exception("idle send failed")


# ==================== ЗАПУСК ====================
async def main():
    load_all()
    bot = Bot(BOT_TOKEN)
    asyncio.create_task(idle_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
