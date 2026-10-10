from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime
from db.models import Role, AkfaTask, Order, Assignment, AssignmentStatus
from db.repository import get_user_by_tg
from states import AkfaCreate

router = Router()

@router.message(F.text.startswith("/akfa "))
async def create_akfa(message: Message, session: AsyncSession, state: FSMContext):
    u = await get_user_by_tg(session, message.from_user.id)
    if not u or u.role != Role.WELDER: return
    try: oid = int(message.text.split()[1])
    except:
        await message.answer("/akfa ORDER_ID"); return
    a = await session.scalar(select(Assignment).where(
        Assignment.order_id == oid, Assignment.user_id == u.id,
        Assignment.role == Role.WELDER, Assignment.status == AssignmentStatus.ACTIVE
    ))
    if not a:
        await message.answer("Bu buyurtma sizga biriktirilmagan."); return
    old = await session.scalar(select(AkfaTask).where(AkfaTask.order_id == oid))
    if old:
        await message.answer("Akfa topshirig'i mavjud."); return
    await state.set_state(AkfaCreate.text)
    await state.update_data(order_id=oid)
    await message.answer("O'lcham va topshiriqni yozing:")

@router.message(AkfaCreate.text)
async def save_akfa(message: Message, session: AsyncSession, state: FSMContext):
    u = await get_user_by_tg(session, message.from_user.id)
    d = await state.get_data()
    oid = d["order_id"]
    session.add(AkfaTask(order_id=oid, created_by_welder_id=u.id, text=message.text))
    o = await session.get(Order, oid)
    o.has_akfa_task = True
    await session.commit()
    await state.clear()
    await message.answer("✅ Akfa topshirig'i yaratildi.")

@router.message(F.text == "🪟 Akfa topshiriqlari")
async def list_akfa(message: Message, session: AsyncSession):
    u = await get_user_by_tg(session, message.from_user.id)
    if not u or u.role != Role.AKFA: return
    tasks = list((await session.scalars(select(AkfaTask).where(AkfaTask.status.in_(["waiting","active"])))).all())
    if not tasks:
        await message.answer("Topshiriq yo'q."); return
    for t in tasks:
        if t.assigned_to_id and t.assigned_to_id != u.id: continue
        cb = f"akfaclaim:{t.id}" if t.status=="waiting" else f"akfadone:{t.id}"
        txt = "📥 Qabul qilish" if t.status=="waiting" else "✅ Tayyor"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=txt, callback_data=cb)]])
        await message.answer(f"🪟 Akfa #{t.id}\nBuyurtma #{t.order_id}\n{t.text}", reply_markup=kb)

@router.callback_query(F.data.startswith("akfaclaim:"))
async def claim_akfa(callback: CallbackQuery, session: AsyncSession):
    u = await get_user_by_tg(session, callback.from_user.id)
    tid = int(callback.data.split(":")[1])
    result = await session.execute(
        update(AkfaTask)
        .where(AkfaTask.id == tid, AkfaTask.status == "waiting")
        .values(status="active", assigned_to_id=u.id)
        .returning(AkfaTask.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        await callback.answer("Boshqa Akfachi olib bo'lgan.", show_alert=True); return
    await session.commit()
    await callback.answer("Sizga biriktirildi.", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tayyor", callback_data=f"akfadone:{tid}")
    ]]))

@router.callback_query(F.data.startswith("akfadone:"))
async def done_akfa(callback: CallbackQuery, session: AsyncSession):
    u = await get_user_by_tg(session, callback.from_user.id)
    tid = int(callback.data.split(":")[1])
    t = await session.get(AkfaTask, tid)
    if not t or t.assigned_to_id != u.id or t.status != "active":
        await callback.answer("Bu ish sizda aktiv emas.", show_alert=True); return
    t.status = "done"
    t.completed_at = datetime.utcnow()
    await session.commit()
    await callback.answer("Akfa tayyor ✅", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)
