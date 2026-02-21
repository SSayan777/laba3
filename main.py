import asyncio
import json
import logging
import os
import random
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv


load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = (os.getenv("ADMIN_USERNAME") or "").lstrip("@").lower()

if not TOKEN:
    raise ValueError("BOT_TOKEN is missing in .env")
if not ADMIN_USERNAME:
    raise ValueError("ADMIN_USERNAME is missing in .env")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

RESULTS_FILE = "results.json"
TOTAL_QUESTIONS = 5


class Quiz(StatesGroup):
    waiting_for_answer = State()
    choosing_numbers = State()
    choosing_one_number = State()


def safe_user_name(message_or_callback: Message | CallbackQuery) -> str:
    user = (
        message_or_callback.from_user
        if isinstance(message_or_callback, CallbackQuery)
        else message_or_callback.from_user
    )
    if not user:
        return "пользователь"
    return user.first_name or user.full_name or "пользователь"


def is_admin_username(username: str | None) -> bool:
    return bool(username) and username.lower() == ADMIN_USERNAME


def load_results() -> list[dict]:
    if not os.path.exists(RESULTS_FILE):
        return []
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            migrated: list[dict] = []
            for key, records in data.items():
                if not isinstance(records, list):
                    continue
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    migrated.append(
                        {
                            "user_id": int(key) if str(key).isdigit() else None,
                            "username": record.get("username"),
                            "name": record.get("name"),
                            "mode": record.get("mode", "Неизвестный режим"),
                            "score": int(record.get("score", 0)),
                            "total": int(record.get("total", TOTAL_QUESTIONS)),
                            "percent": int(record.get("percent", 0)),
                            "grade": int(record.get("grade", 2)),
                            "created_at_utc": record.get("created_at_utc"),
                        }
                    )
            return migrated
        return []
    except (json.JSONDecodeError, OSError):
        return []


def save_results(data: list[dict]) -> None:
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def grade_by_score(score: int, total: int) -> int:
    ratio = score / total if total else 0
    if ratio >= 0.9:
        return 5
    if ratio >= 0.75:
        return 4
    if ratio >= 0.5:
        return 3
    return 2


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="Проверка на одну цифру", callback_data="one_number")],
        [InlineKeyboardButton(text="Случайные примеры", callback_data="random_mode")],
        [InlineKeyboardButton(text="Несколько чисел на выбор", callback_data="choose_numbers")],
        [InlineKeyboardButton(text="Мои результаты", callback_data="my_results")],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton(text="Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Остановить тест", callback_data="stop_test")],
            [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")],
        ]
    )


def result_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    return main_menu(is_admin=is_admin)


def numbers_selector(selected: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for n in range(2, 10):
        prefix = "✅ " if n in selected else ""
        row.append(
            InlineKeyboardButton(text=f"{prefix}{n}", callback_data=f"toggle_num_{n}")
        )
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="Начать тест", callback_data="start_chosen")])
    rows.append([InlineKeyboardButton(text="Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def one_number_selector() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for n in range(2, 10):
        row.append(InlineKeyboardButton(text=str(n), callback_data=f"one_num_{n}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def next_question(mode: str, fixed_number: int | None, selected_numbers: list[int] | None) -> tuple[int, int]:
    if mode == "one_number" and fixed_number is not None:
        return fixed_number, random.randint(1, 10)
    if mode == "selected_numbers" and selected_numbers:
        return random.choice(selected_numbers), random.randint(1, 10)
    return random.randint(1, 10), random.randint(1, 10)


async def ask_question(chat_message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    a, b = next_question(
        mode=data["mode"],
        fixed_number=data.get("fixed_number"),
        selected_numbers=data.get("selected_numbers"),
    )
    await state.update_data(a=a, b=b)
    await chat_message.answer(f"{a} x {b} = ?", reply_markup=stop_keyboard())


async def start_quiz(
    callback: CallbackQuery,
    state: FSMContext,
    mode: str,
    mode_title: str,
    fixed_number: int | None = None,
    selected_numbers: list[int] | None = None,
) -> None:
    await state.update_data(
        mode=mode,
        mode_title=mode_title,
        fixed_number=fixed_number,
        selected_numbers=selected_numbers or [],
        score=0,
        answered=0,
        total=TOTAL_QUESTIONS,
    )
    await state.set_state(Quiz.waiting_for_answer)
    await callback.answer()
    await ask_question(callback.message, state)


async def finalize_quiz(
    message: Message,
    state: FSMContext,
    stopped: bool,
    user_override=None,
) -> None:
    data = await state.get_data()
    if not data or "total" not in data or "mode_title" not in data:
        is_admin = is_admin_username(message.from_user.username if message.from_user else None)
        await message.answer("Нет активного теста.", reply_markup=result_keyboard(is_admin=is_admin))
        await state.clear()
        return

    score = int(data.get("score", 0))
    answered = int(data.get("answered", 0))
    total = int(data.get("total", TOTAL_QUESTIONS))

    grade = grade_by_score(score, total)
    percent = int(round((score / total) * 100)) if total else 0

    user = user_override if user_override is not None else message.from_user
    result = {
        "user_id": user.id if user else None,
        "username": user.username if user else None,
        "name": user.full_name if user else None,
        "mode": data.get("mode_title"),
        "score": score,
        "total": total,
        "answered": answered,
        "percent": percent,
        "grade": grade,
        "stopped": stopped,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    results = load_results()
    results.append(result)
    save_results(results)

    is_admin = is_admin_username(user.username if user else None)
    prefix = ""
    if stopped:
        prefix = "Тест остановлен.\nНеотвеченные вопросы засчитаны как неверные.\n\n"

    await message.answer(
        f"{prefix}Результат:\n"
        f"Правильных ответов: {score}/{total}\n"
        f"Процент: {percent}%\n"
        f"Оценка: {grade}",
        reply_markup=result_keyboard(is_admin=is_admin),
    )
    await state.clear()


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    name = safe_user_name(message)
    is_admin = is_admin_username(message.from_user.username if message.from_user else None)
    await message.answer(
        f"Привет, {name}!\n"
        "Я помогу выучить таблицу умножения.\n"
        "Выберите режим:",
        reply_markup=main_menu(is_admin=is_admin),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Доступные команды:\n"
        "/start - главное меню\n"
        "/help - справка\n"
        "/stop - остановить текущий тест\n\n"
        "Режимы:\n"
        "1) Проверка на одну цифру\n"
        "2) Случайные примеры\n"
        "3) Несколько чисел на выбор"
    )


@dp.message(Command("stop"))
async def cmd_stop(message: Message, state: FSMContext) -> None:
    await finalize_quiz(message, state, stopped=True)


@dp.callback_query(F.data == "main_menu")
async def go_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state == Quiz.waiting_for_answer.state:
        await callback.answer()
        await finalize_quiz(callback.message, state, stopped=True, user_override=callback.from_user)
        return
    await state.clear()
    is_admin = is_admin_username(callback.from_user.username if callback.from_user else None)
    await callback.answer()
    await callback.message.answer("Главное меню:", reply_markup=main_menu(is_admin=is_admin))


@dp.callback_query(F.data == "one_number")
async def one_number(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Quiz.choosing_one_number)
    await callback.answer()
    await callback.message.answer(
        "Выберите цифру для проверки:",
        reply_markup=one_number_selector(),
    )


@dp.callback_query(Quiz.choosing_one_number, F.data.startswith("one_num_"))
async def one_number_selected(callback: CallbackQuery, state: FSMContext) -> None:
    number = int(callback.data.split("_")[-1])
    await start_quiz(
        callback=callback,
        state=state,
        mode="one_number",
        mode_title=f"Одна цифра ({number})",
        fixed_number=number,
    )


@dp.callback_query(F.data == "random_mode")
async def random_mode(callback: CallbackQuery, state: FSMContext) -> None:
    await start_quiz(
        callback=callback,
        state=state,
        mode="random_mode",
        mode_title="Случайные примеры",
    )


@dp.callback_query(F.data == "choose_numbers")
async def choose_numbers(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Quiz.choosing_numbers)
    await state.update_data(selected_numbers=[])
    await callback.answer()
    await callback.message.answer(
        "Выберите одну или несколько цифр от 2 до 9:",
        reply_markup=numbers_selector(set()),
    )


@dp.callback_query(Quiz.choosing_numbers, F.data.startswith("toggle_num_"))
async def toggle_number(callback: CallbackQuery, state: FSMContext) -> None:
    number = int(callback.data.split("_")[-1])
    data = await state.get_data()
    selected = set(data.get("selected_numbers", []))

    if number in selected:
        selected.remove(number)
    else:
        selected.add(number)

    await state.update_data(selected_numbers=sorted(selected))
    await callback.answer("Выбрано")
    try:
        await callback.message.edit_reply_markup(reply_markup=numbers_selector(selected))
    except TelegramBadRequest:
        pass


@dp.callback_query(Quiz.choosing_numbers, F.data == "start_chosen")
async def start_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = data.get("selected_numbers", [])
    if not selected:
        await callback.answer("Выберите хотя бы одну цифру", show_alert=True)
        return

    await start_quiz(
        callback=callback,
        state=state,
        mode="selected_numbers",
        mode_title=f"Несколько чисел: {', '.join(map(str, selected))}",
        selected_numbers=selected,
    )


@dp.callback_query(F.data == "stop_test")
async def stop_test(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Остановлено")
    await finalize_quiz(callback.message, state, stopped=True, user_override=callback.from_user)


@dp.message(Quiz.waiting_for_answer)
async def process_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        user_answer = int((message.text or "").strip())
    except ValueError:
        await message.answer("Введите число, например: 42")
        return

    a = data["a"]
    b = data["b"]
    score = int(data["score"])
    answered = int(data["answered"]) + 1
    total = int(data["total"])

    if user_answer == a * b:
        score += 1
        await message.answer("Верно")
    else:
        await message.answer(f"Неверно. Правильный ответ: {a * b}")

    await state.update_data(score=score, answered=answered)

    if answered >= total:
        grade = grade_by_score(score, total)
        percent = int(round((score / total) * 100))
        user = message.from_user
        result = {
            "user_id": user.id if user else None,
            "username": user.username if user else None,
            "name": user.full_name if user else None,
            "mode": data["mode_title"],
            "score": score,
            "total": total,
            "percent": percent,
            "grade": grade,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        results = load_results()
        results.append(result)
        save_results(results)

        is_admin = is_admin_username(user.username if user else None)
        await message.answer(
            "Результат:\n"
            f"Правильных ответов: {score}/{total}\n"
            f"Процент: {percent}%\n"
            f"Оценка: {grade}",
            reply_markup=result_keyboard(is_admin=is_admin),
        )
        await state.clear()
        return

    await ask_question(message, state)


@dp.callback_query(F.data == "my_results")
async def my_results(callback: CallbackQuery) -> None:
    user = callback.from_user
    if not user:
        await callback.answer("Ошибка пользователя", show_alert=True)
        return

    results = load_results()
    user_results = [r for r in results if r.get("user_id") == user.id]
    await callback.answer()

    if not user_results:
        await callback.message.answer("У вас пока нет сохраненных результатов.")
        return

    last_results = user_results[-10:]
    lines = ["Ваши последние результаты:"]
    for r in reversed(last_results):
        stopped_mark = " (остановлен)" if r.get("stopped") else ""
        lines.append(
            f"{r.get('mode', '-')}: {r.get('score', 0)}/{r.get('total', TOTAL_QUESTIONS)}, "
            f"{r.get('percent', 0)}%, оценка {r.get('grade', 2)}{stopped_mark}"
        )
    await callback.message.answer("\n".join(lines))


@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery) -> None:
    if not is_admin_username(callback.from_user.username if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return

    results = load_results()
    await callback.answer()
    if not results:
        await callback.message.answer("Сохраненных результатов пока нет.")
        return

    users = {r.get("user_id") for r in results if r.get("user_id") is not None}
    average_percent = round(sum(r.get("percent", 0) for r in results) / len(results), 1)
    top = sorted(results, key=lambda x: x.get("percent", 0), reverse=True)[:5]

    summary = [
        "Админ-панель:",
        f"Всего попыток: {len(results)}",
        f"Уникальных пользователей: {len(users)}",
        f"Средний результат: {average_percent}%",
        "",
        "Топ-5 результатов:",
    ]
    for item in top:
        display = item.get("name") or (f"@{item.get('username')}" if item.get("username") else "Без имени")
        summary.append(
            f"{display}: {item.get('score', 0)}/{item.get('total', TOTAL_QUESTIONS)} "
            f"({item.get('percent', 0)}%), оценка {item.get('grade', 2)}"
        )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Очистить все результаты", callback_data="admin_clear_results")],
            [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")],
        ]
    )
    await callback.message.answer("\n".join(summary), reply_markup=keyboard)


@dp.callback_query(F.data == "admin_clear_results")
async def admin_clear_results(callback: CallbackQuery) -> None:
    if not is_admin_username(callback.from_user.username if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    save_results([])
    await callback.answer("Очищено")
    await callback.message.answer("Все результаты удалены.")


@dp.message()
async def fallback(message: Message) -> None:
    await message.answer("Используйте /start для начала или /help для справки.")


async def main() -> None:
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
