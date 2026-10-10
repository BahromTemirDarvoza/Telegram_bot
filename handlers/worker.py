from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Role, Assignment, AssignmentStatus, Order, OrderMedia, User, AkfaTask
from db.repository import get_user_by_tg, list_available_orders, claim_order, complete_assignment
from keyboards.common import claim_kb, complete_kb, welder_complete_kb
from utils import money
from states import FinishPainter, AkfaDimensions

from aiogram.fsm.context import FSMContext



router = Router()

def order_text(o):
    dl = o.deadline.strftime("%d.%m.%Y") if o.deadline else "Muddat yo'q"
    return (
        f"📦 #{o.id} — {o.title}\n"
        f"📝 {o.description}\n"
        f"👤 {o.customer_name}\n"
        f"📞 {o.customer_phone}\n"
        f"📍 {o.customer_address}\n"
        f"💵 {money(o.price)}\n"
        f"📅 {dl}\n"
        f"⚡ {o.priority.value}"
    )


async def photos_for_role(session, order_id, role):
    # Buyurtmaning dastlabki rasmlari
    original_photos = list(
        (
            await session.scalars(
                select(OrderMedia)
                .where(OrderMedia.order_id == order_id)
                .order_by(OrderMedia.position, OrderMedia.id)
            )
        ).all()
    )

    photo_ids = [p.file_id for p in original_photos]

    # Keyingi ishchi oldingi ishchining rasmini ham ko'radi
    previous_role = {
        Role.PAINTER: Role.WELDER,
        Role.COURIER: Role.PAINTER
    }.get(role)

    if previous_role:
        previous_assignment = await session.scalar(
            select(Assignment)
            .where(
                Assignment.order_id == order_id,
                Assignment.role == previous_role,
                Assignment.status == AssignmentStatus.DONE
            )
            .order_by(Assignment.completed_at.desc())
        )

        if (
            previous_assignment
            and previous_assignment.completion_photo_file_id
        ):
            photo_ids.append(
                previous_assignment.completion_photo_file_id
            )

    return photo_ids


@router.message(F.text == "💰 Mening balansim")
async def balance(message: Message, session: AsyncSession):
    u = await get_user_by_tg(session, message.from_user.id)
    await message.answer(f"💰 Balansingiz: {money(u.balance)}")

@router.message(F.text == "📥 Mavjud buyurtmalar")
async def available(message: Message, session: AsyncSession):
    u = await get_user_by_tg(session, message.from_user.id)
    if u.role not in {Role.WELDER, Role.PAINTER, Role.COURIER}: return
    orders = await list_available_orders(session, u.role)
    if not orders:
        await message.answer("Hozir mavjud buyurtma yo'q.")
        return
    for o in orders:
        await message.answer(order_text(o), reply_markup=claim_kb(o.id))

@router.callback_query(F.data.startswith("akfa:"))
async def akfa_request(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    u = await get_user_by_tg(session, callback.from_user.id)

    if not u or u.role != Role.WELDER:
        await callback.answer(
            "Bu tugma faqat payvandchi uchun.",
            show_alert=True
        )
        return

    oid = int(callback.data.split(":")[1])
    order = await session.get(Order, oid)

    if not order:
        await callback.answer(
            "Buyurtma topilmadi.",
            show_alert=True
        )
        return

    await state.set_state(AkfaDimensions.dimensions)
    await state.update_data(order_id=oid)

    await callback.message.answer(
        f"📦 Buyurtma №{order.id}\n"
        "📏 Deraza o‘lchamlarini kiriting:"
    )
    await callback.answer()




@router.callback_query(F.data.startswith("claim:"))
async def claim(
    callback: CallbackQuery,
    session: AsyncSession
):
    u = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    if not u:
        await callback.answer(
            "Foydalanuvchi topilmadi.",
            show_alert=True
        )
        return

    oid = int(callback.data.split(":")[1])

    ok, msg = await claim_order(
        session,
        oid,
        u
    )

    await callback.answer(msg, show_alert=True)

    if not ok:
        return

    # Kuryer buyurtmani qabul qilganda Akfa vazifasini tekshirish
    if u.role == Role.COURIER:
        akfa_task = await session.scalar(
            select(AkfaTask).where(
                AkfaTask.order_id == oid
            )
        )

        order = await session.get(Order, oid)

        if akfa_task or (
            order and order.has_akfa_task
        ):
            try:
                await callback.bot.send_message(
                    chat_id=u.telegram_id,
                    text=(
                        f"🔔 Eslatma: №{oid} buyurtma.\n\n"
                        "Akfachidan derazani oldingizmi?\n"
                        "Iltimos, deraza olinganini tekshiring."
                    )
                )
            except Exception:
                import logging
                logging.exception(
                    "Akfa eslatmasini yuborishda xatolik: "
                    "order_id=%s",
                    oid
                )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        "✅ Ish sizga biriktirildi.\n\n"
        "📋 Buyurtma ma'lumotlarini ko'rish uchun "
        "quyidagi tugmani bosing.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📋 Buyurtmani ko'rish",
                        callback_data=f"vieworder:{oid}"
                    )
                ]
            ]
        )
    )



@router.callback_query(F.data.startswith("vieworder:"))
async def view_order(
    callback: CallbackQuery,
    session: AsyncSession
):
    u = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    oid = int(callback.data.split(":")[1])

    assignment = await session.scalar(
        select(Assignment).where(
            Assignment.order_id == oid,
            Assignment.user_id == u.id,
            Assignment.status == AssignmentStatus.ACTIVE
        )
    )

    if not assignment:
        await callback.answer(
            "Bu buyurtma sizga biriktirilmagan.",
            show_alert=True
        )
        return

    order = await session.get(Order, oid)

    if not order:
        await callback.answer(
            "Buyurtma topilmadi.",
            show_alert=True
        )
        return

    # Ishchining roliga mos rasmlarni olish
    photo_ids = await photos_for_role(
        session,
        oid,
        u.role
    )

    finish_kb = (
        welder_complete_kb(oid)
        if u.role == Role.WELDER
        else complete_kb(oid)
    )

    caption = order_text(order)

    if len(photo_ids) == 1:
        await callback.message.answer_photo(
            photo=photo_ids[0],
            caption=caption,
            reply_markup=finish_kb
        )

    elif len(photo_ids) > 1:
        # Telegram albomida ko'pi bilan 10 ta rasm yuboramiz
        for start in range(0, len(photo_ids), 10):
            batch = photo_ids[start:start + 10]

            album = [
                InputMediaPhoto(
                    media=photo_id,
                    caption=caption if start == 0 and i == 0 else None
                )
                for i, photo_id in enumerate(batch)
            ]

            await callback.message.answer_media_group(
                media=album
            )

        await callback.message.answer(
            f"📦 Buyurtma №{order.id}\n"
            "Ishni yakunlash uchun quyidagi tugmani bosing.",
            reply_markup=finish_kb
        )

    else:
        await callback.message.answer(
            caption,
            reply_markup=finish_kb
        )

    await callback.answer("📋 Buyurtma ochildi.")



@router.message(F.text == "🧰 Mening ishlarim")
async def my_jobs(
    message: Message,
    session: AsyncSession
):
    u = await get_user_by_tg(
        session,
        message.from_user.id
    )

    items = list(
        (
            await session.scalars(
                select(Assignment)
                .where(
                    Assignment.user_id == u.id,
                    Assignment.status == AssignmentStatus.ACTIVE
                )
                .order_by(Assignment.accepted_at)
            )
        ).all()
    )

    if not items:
        await message.answer("📭 Sizda faol buyurtmalar yo'q.")
        return

    for a in items:
        o = await session.get(Order, a.order_id)

        if not o:
            continue

        # Ishchining roliga mos rasmlarni olish
        photo_ids = await photos_for_role(
            session,
            o.id,
            u.role
        )

        caption = order_text(o)

        finish_kb = (
            welder_complete_kb(o.id)
            if u.role == Role.WELDER
            else complete_kb(o.id)
        )

        try:
            if len(photo_ids) == 1:
                await message.answer_photo(
                    photo=photo_ids[0],
                    caption=caption,
                    reply_markup=finish_kb
                )

            elif len(photo_ids) > 1:
                for start in range(0, len(photo_ids), 10):
                    batch = photo_ids[start:start + 10]

                    album = [
                        InputMediaPhoto(
                            media=photo_id,
                            caption=(
                                caption
                                if start == 0 and i == 0
                                else None
                            )
                        )
                        for i, photo_id in enumerate(batch)
                    ]

                    await message.answer_media_group(
                        media=album
                    )

                await message.answer(
                    f"📦 Buyurtma №{o.id}\n"
                    "Ishni yakunlash uchun tugmani bosing.",
                    reply_markup=finish_kb
                )

            else:
                await message.answer(
                    caption,
                    reply_markup=finish_kb
                )

        except Exception:
            import logging
            logging.exception(
                "Mening ishlarimda xatolik: order_id=%s",
                o.id
            )
            await message.answer(
                f"❌ №{o.id} buyurtmani ko'rsatishda xatolik yuz berdi."
            )


@router.callback_query(F.data.startswith("done:"))
async def done(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    u = await get_user_by_tg(session, callback.from_user.id)
    oid = int(callback.data.split(":")[1])

    if u.role in {
        Role.WELDER,
        Role.PAINTER,
        Role.COURIER
    }:
        await state.set_state(FinishPainter.photo)
        await state.update_data(order_id=oid)

        role_text = {
            Role.WELDER: "🔧 Payvandlangan mahsulot",
            Role.PAINTER: "🎨 Bo‘yalgan mahsulot",
            Role.COURIER: "🚚 Mijozga yetkazilgan mahsulot"
        }

        await callback.message.answer(
            f"{role_text[u.role]} rasmini yuboring."
        )
        await callback.answer()
        return

    ok, msg = await complete_assignment(session, oid, u)
    await callback.answer(msg, show_alert=True)

    if ok:
        await callback.message.edit_reply_markup(reply_markup=None)




@router.message(FinishPainter.photo, F.photo)
async def painter_photo(
    message: Message,
    session: AsyncSession,
    state: FSMContext
):
    data = await state.get_data()
    order_id = data.get("order_id")

    user = await get_user_by_tg(
        session,
        message.from_user.id
    )

    if not user or not order_id:
        await state.clear()
        await message.answer(
            "❌ Foydalanuvchi yoki buyurtma topilmadi."
        )
        return

    # Faqat bitta yakuniy rasm qabul qilinadi
    if data.get("photo_sent"):
        await message.answer(
            "❌ Faqat 1 ta rasm yuborish mumkin."
        )
        return

    photo_id = message.photo[-1].file_id

    # Rasm yuborilganini belgilash
    await state.update_data(photo_sent=True)

    ok, msg = await complete_assignment(
        session,
        order_id,
        user,
        photo_id
    )

    await state.clear()

    if not ok:
        await message.answer("❌ " + msg)
        return

    await message.answer("✅ " + msg)

    # Faqat kuryer yakunlaganda adminlarga xabar yuboriladi
    if user.role != Role.COURIER:
        return

    order = await session.get(Order, order_id)

    if not order:
        return

    admins = list(
        (
            await session.scalars(
                select(User).where(
                    User.role.in_([
                        Role.SUPER_ADMIN,
                        Role.ASSISTANT_ADMIN
                    ])
                )
            )
        ).all()
    )

    # Buyurtmaning dastlabki rasmlarini olish
    original_photos = list(
        (
            await session.scalars(
                select(OrderMedia)
                .where(OrderMedia.order_id == order_id)
                .order_by(
                    OrderMedia.position,
                    OrderMedia.id
                )
            )
        ).all()
    )

    # Dastlabki rasmlar va kuryerning yakuniy rasmi
    all_photo_ids = [
        p.file_id for p in original_photos
    ]

    if photo_id not in all_photo_ids:
        all_photo_ids.append(photo_id)

    caption = (
        "✅ BUYURTMA YAKUNLANDI\n\n"
        f"{order_text(order)}\n\n"
        f"🚚 Kuryer: {user.full_name}\n"
        "📸 Yakuniy yetkazib berish rasmi"
    )

    for admin in admins:
        try:
            # Telegram albomida bir martada 10 tagacha rasm
            for start in range(0, len(all_photo_ids), 10):
                batch = all_photo_ids[start:start + 10]

                album = [
                    InputMediaPhoto(
                        media=pid,
                        caption=(
                            caption
                            if start == 0 and i == 0
                            else None
                        )
                    )
                    for i, pid in enumerate(batch)
                ]

                await message.bot.send_media_group(
                    chat_id=admin.telegram_id,
                    media=album
                )

        except Exception:
            import logging
            logging.exception(
                "Adminga buyurtma rasmlarini yuborishda xatolik: "
                "admin_id=%s, order_id=%s",
                admin.telegram_id,
                order_id
            )


@router.message(AkfaDimensions.dimensions, F.text)
async def send_akfa_dimensions(
    message: Message,
    session: AsyncSession,
    state: FSMContext
):
    u = await get_user_by_tg(
        session,
        message.from_user.id
    )

    if not u or u.role != Role.WELDER:
        await message.answer(
            "Bu amal faqat payvandchi uchun."
        )
        await state.clear()
        return

    data = await state.get_data()
    oid = data.get("order_id")

    if not oid:
        await message.answer(
            "Buyurtma ma’lumoti topilmadi."
        )
        await state.clear()
        return

    order = await session.get(Order, oid)

    if not order:
        await message.answer(
            "Buyurtma topilmadi."
        )
        await state.clear()
        return

    akfachilar = list(
        (
            await session.scalars(
                select(User).where(
                    User.role == Role.AKFA,
                    User.is_active == True
                )
            )
        ).all()
    )

    if not akfachilar:
        await message.answer(
            "❌ Akfachi topilmadi."
        )
        await state.clear()
        return

    # Akfachiga yuboriladigan xabar
    xabar = (
        "🪟 AKFA UCHUN O'LCHAMLAR\n\n"
        f"📦 Buyurtma nomi: {order.title}\n"
        f"🔢 Buyurtma raqami: #{order.id}\n"
        f"📏 O'lchamlar: {message.text}"
    )

    yuborildi = False

    for akfa in akfachilar:
        try:
            await message.bot.send_message(
                chat_id=akfa.telegram_id,
                text=xabar
            )
            yuborildi = True

        except Exception:
            import logging
            logging.exception(
                "Akfachiga xabar yuborilmadi: user_id=%s",
                akfa.id
            )

    if yuborildi:
        # Shu buyurtma uchun Akfa vazifasi bor-yo'qligini tekshiramiz
        akfa_task = await session.scalar(
            select(AkfaTask).where(
                AkfaTask.order_id == oid
            )
        )

        if akfa_task:
            # Vazifa avval yaratilgan bo'lsa, ma'lumotlarini yangilaymiz
            akfa_task.text = message.text
            akfa_task.created_by_welder_id = u.id
            akfa_task.status = "waiting"
            akfa_task.assigned_to_id = None
            akfa_task.completed_at = None
        else:
            # Yangi Akfa vazifasini bazaga saqlaymiz
            akfa_task = AkfaTask(
                order_id=oid,
                created_by_welder_id=u.id,
                text=message.text,
                status="waiting"
            )
            session.add(akfa_task)

        # Buyurtmada Akfa vazifasi borligini belgilaymiz
        order.has_akfa_task = True

        await session.commit()

        await message.answer(
            "✅ O'lchamlar Akfachiga yuborildi!\n"
            "✅ Akfa vazifasi bazaga saqlandi."
        )
    else:
        await message.answer(
            "❌ Xabar Akfachiga yuborilmadi. "
            "Akfachi botda /start bosganini tekshiring."
        )

    await state.clear()
