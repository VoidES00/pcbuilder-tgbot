import aiogram, asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
#чат гпт сделай тг бота ну крч который типо делает вот это и это, и ещё вон то


inline_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="кнопка 1"
            ),
            InlineKeyboardButton(
                text="кнопка 2"
            ),
            InlineKeyboardButton(
                text="кнопка 3"
            ),
            InlineKeyboardButton(
                text="кнопка 4"
            ),
        ]
    ]
)

dp=Dispatcher()
@dp.message(CommandStart())
async def command_start(message: Message):
    await message.answer(f"ну крч типо крутой текст фвфывфвфцвффцвоваришгварщшыаооо", reply_markup=inline_kb)
async def main():
    bot=Bot(token="8953531728:AAF5v3AjvJ0JjSmARDdwTFUkedupbJ0b0EY", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)
if __name__ == "__main__":
    print("бот работает, иди тестируй")
    asyncio.run(main())
