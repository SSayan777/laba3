import asyncio
import random
import json
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ===== ЗАГРУЗКА ТОКЕНА =====
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

RESULTS_FILE = "results.json"

# ===== FSM =====
class Quiz(StatesGroup):
    waiting_number = State()
    choosing_multi = State()
    question = State()

# ===== МЕНЮ =====
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📘 Выбрать одно число", callback_data="choose_number")],
        [InlineKeyboardButton(text="🎲 Случайные примеры", callback_data="random_mode")],
        [InlineKeyboardButton(text="📚 Несколько чисел", callback_data="multi_mode")],
        [InlineKeyboardButton(text="📖 Таблица умножения", callback_data="table")],
        [InlineKeyboardButton(text="📊 Мои результаты", callback_data="results")]
    ])

def continue_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Продолжить", callback_data="continue")]
    ])

# ===== ОЦЕНКА =====
def get_grade(percent):
    if percent >= 90:
        return 5
    elif percent >= 70:
        return 4
    elif percent >= 50:
        return 3
    else:
        return 2

# ===== СОХРАНЕНИЕ =====
def save_result(user_id, name, percent):
    grade = get_grade(percent)

    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = {}

    if str(user_id) not in data:
        data[str(user_id)] = []

    data[str(user_id)].append({
        "name": name,
        "percent": percent,
        "grade": grade
    })

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ===== START =====
@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        f"Привет, <b>{message.from_user.first_name}</b> 👋\nВыбери режим:",
        reply_markup=main_menu()
    )

@dp.callback_query(F.data == "continue")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Главное меню:", reply_markup=main_menu())

# ===== ТАБЛИЦА УМНОЖЕНИЯ =====
@dp.callback_query(F.data == "table")
async def show_table(callback: CallbackQuery):
    text = "📖 <b>Таблица умножения</b>\n\n"
    for i in range(2, 10):
        text += f"<b>На {i}:</b>\n"
        for j in range(2, 10):
            text += f"{j} × {i} = {j*i}\n"
        text += "\n"

    await callback.message.answer(text, reply_markup=continue_button())

# ===== РЕЗУЛЬТАТЫ =====
@dp.callback_query(F.data == "results")
async def show_results(callback: CallbackQuery):
    try:
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        await callback.message.answer("У вас пока нет результатов.")
        return

    user_data = data.get(str(callback.from_user.id))
    if not user_data:
        await callback.message.answer("У вас пока нет результатов.")
        return

    text = "📊 Ваши результаты:\n\n"
    for i, attempt in enumerate(user_data, 1):
        text += f"Попытка {i}: {attempt['percent']}% (оценка {attempt['grade']})\n"

    await callback.message.answer(text)

# ===== ОДНО ЧИСЛО =====
@dp.callback_query(F.data == "choose_number")
async def choose_number(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите число от 2 до 9:")
    await state.set_state(Quiz.waiting_number)

@dp.message(Quiz.waiting_number)
async def get_number(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введите число от 2 до 9!")
        return

    number = int(message.text)
    if number < 2 or number > 9:
        await message.answer("Число должно быть от 2 до 9!")
        return

    await state.update_data(mode="single", base=number, multiplier=2, correct=0)
    await ask_single_question(message, state)

async def ask_single_question(message, state):
    data = await state.get_data()

    if data["multiplier"] > 9:
        percent = int((data["correct"] / 8) * 100)
        save_result(message.from_user.id, message.from_user.first_name, percent)

        await message.answer(
            f"📊 Тест завершён!\n"
            f"Правильных ответов: {percent}%\n"
            f"🎓 Оценка: <b>{get_grade(percent)}</b>",
            reply_markup=continue_button()
        )
        await state.clear()
        return

    a = data["multiplier"]
    b = data["base"]

    await message.answer(f"{a} × {b} = ?")
    await state.set_state(Quiz.question)

# ===== RANDOM =====
@dp.callback_query(F.data == "random_mode")
async def random_mode(callback: CallbackQuery, state: FSMContext):
    await state.update_data(mode="random", count=0, correct=0)
    await ask_random_question(callback.message, state)

# ===== MULTI С ВЫБОРОМ =====
def multi_keyboard(selected):
    buttons = []
    row = []
    for i in range(2, 10):
        text = f"✅ {i}" if i in selected else str(i)
        row.append(InlineKeyboardButton(text=text, callback_data=f"num_{i}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    buttons.append([InlineKeyboardButton(text="▶ Начать тест", callback_data="start_multi")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.callback_query(F.data == "multi_mode")
async def multi_mode(callback: CallbackQuery, state: FSMContext):
    await state.update_data(selected_numbers=[])
    await state.set_state(Quiz.choosing_multi)
    await callback.message.answer("Выберите числа:", reply_markup=multi_keyboard([]))

@dp.callback_query(F.data.startswith("num_"))
async def select_number(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_numbers", [])

    number = int(callback.data.split("_")[1])
    if number in selected:
        selected.remove(number)
    else:
        selected.append(number)

    await state.update_data(selected_numbers=selected)
    await callback.message.edit_reply_markup(reply_markup=multi_keyboard(selected))

@dp.callback_query(F.data == "start_multi")
async def start_multi(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_numbers", [])

    if not selected:
        await callback.answer("Выберите хотя бы одно число!", show_alert=True)
        return

    await state.update_data(mode="multi", bases=selected, count=0, correct=0)
    await ask_random_question(callback.message, state)

# ===== ВОПРОСЫ =====
async def ask_random_question(message, state):
    data = await state.get_data()

    if data["count"] >= 5:
        percent = int((data["correct"] / 5) * 100)
        save_result(message.from_user.id, message.from_user.first_name, percent)

        await message.answer(
            f"📊 Тест завершён!\n"
            f"Правильных ответов: {percent}%\n"
            f"🎓 Оценка: <b>{get_grade(percent)}</b>",
            reply_markup=continue_button()
        )
        await state.clear()
        return

    if data["mode"] == "multi":
        a = random.choice(data["bases"])
    else:
        a = random.randint(2, 9)

    b = random.randint(2, 9)

    await state.update_data(a=a, b=b)
    await message.answer(f"{a} × {b} = ?")
    await state.set_state(Quiz.question)

# ===== ПРОВЕРКА =====
@dp.message(Quiz.question)
async def check_answer(message: Message, state: FSMContext):
    data = await state.get_data()

    if not message.text.isdigit():
        await message.answer("Введите число!")
        return

    answer = int(message.text)

    if data.get("mode") == "single":
        correct_answer = data["multiplier"] * data["base"]
        multiplier = data["multiplier"] + 1
        correct = data["correct"] + (1 if answer == correct_answer else 0)

        await state.update_data(multiplier=multiplier, correct=correct)
        await ask_single_question(message, state)
    else:
        correct_answer = data["a"] * data["b"]
        count = data["count"] + 1
        correct = data["correct"] + (1 if answer == correct_answer else 0)

        await state.update_data(count=count, correct=correct)
        await ask_random_question(message, state)

# ===== ЗАПУСК =====
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())