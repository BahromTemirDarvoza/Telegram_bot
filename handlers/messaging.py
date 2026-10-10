from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import User, Role
from db.repository import get_user_by_tg
from states import Broadcast

router = Router()

@router.message(F.text == "📢 Xabar yuborish")
async def begin(message: Message, session: AsyncSession, state: FSMContext):
    u = await get_user_by_tg(session, message.from_user.id)
    if not u or u.role not in {Role.SUPER_ADMIN, Role.ASSISTANT_ADMIN}: return
    await state.set_state(Broadcast.target)
    await message.answer("all yoki Telegram ID yuboring.")

@router.message(Broadcast.target)
async def target(message: Message, state: FSMContext):
    t = message.text.strip().lower()
    if t != "all" and not t.isdigit():
        await message.answer("all yoki ID"); return
    await state.update_data(target=t)
    await state.set_state(Broadcast.message)
    await message.answer("Xabar matni:")

@router.message(Broadcast.message)
async def send(message: Message, session: AsyncSession, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if d["target"] == "all":
        users = list((await session.scalars(select(User).where(
            User.is_active.is_(True),
            User.role.in_([Role.WELDER, Role.PAINTER, Role.COURIER, Role.AKFA])
        ))).all())
    else:
        u = await session.scalar(select(User).where(User.telegram_id == int(d["target"])))
        users = [u] if u else []
    sent = 0
    for u in users:
        try:
            await bot.send_message(u.telegram_id, "📢 Admin xabari:\n\n" + message.text)
            sent += 1
        except:
            pass
    await state.clear()
    await message.answer(f"✅ {sent} ta foydalanuvchiga yuborildi.")
