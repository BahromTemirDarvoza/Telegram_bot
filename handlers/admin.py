from aiogram import Router, F

from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from aiogram.types import  (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile, InputMediaPhoto, ReplyKeyboardRemove
)

from states import CreateOrder, AssignRole, SetPercent, Broadcast, WorkerPayment, EditOrder

from aiogram.fsm.context import FSMContext

from sqlalchemy import select, func

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from decimal import Decimal

from db.models import (
    User,
    Role,
    Order,
    OrderMedia,
    OrderStatus,
    Priority,
    Assignment,
    BalanceTransaction,
    AssignmentStatus,
     PayrollPeriod
)

from db.repository import (
    get_user_by_tg,
    create_order,
    get_or_create_settings,
    reset_balance,
    revert_stage
)

from keyboards.common import role_kb, main_menu

from utils import parse_money, parse_deadline, money

from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

router = Router()


async def create_excel_report(session: AsyncSession):
    wb = Workbook()

    # 1-varaq: Ishchilar
    ws = wb.active
    ws.title = "Ishchilar"
    ws.append([
        "Tartib", "Ishchi ismi", "Lavozimi",
        "Joriy balans (so'm)"
    ])

    users = list((
        await session.scalars(
            select(User)
            .where(
                User.role.in_([
                    Role.WELDER,
                    Role.PAINTER,
                    Role.COURIER,
                    Role.AKFA
                ])
            )
            .order_by(User.full_name)
        )
    ).all())

    role_names = {
        Role.WELDER: "Payvandchi",
        Role.PAINTER: "Bo'yoqchi",
        Role.COURIER: "Yetkazuvchi",
        Role.AKFA: "Akfachi"
    }

    for i, user in enumerate(users, 1):
        ws.append([
            i,
            user.full_name,
            role_names.get(user.role, user.role.value),
            float(user.balance or 0)
        ])

    # 2-varaq: Buyurtmalar
    ws = wb.create_sheet("Buyurtmalar")
    ws.append([
        "Buyurtma №", "Nomi", "Mijoz", "Telefon",
        "Manzil", "Narxi (so'm)", "Holati",
        "Ustuvorligi", "Yaratilgan sana", "Muddat"
    ])

    orders = list((
        await session.scalars(
            select(Order).order_by(Order.id)
        )
    ).all())

    for order in orders:
        ws.append([
            order.id,
            order.title,
            order.customer_name,
            order.customer_phone,
            order.customer_address,
            float(order.price or 0),
            order.status.value if order.status else "",
            order.priority.value if order.priority else "",
            order.created_at.replace(tzinfo=None)
                if order.created_at else None,
            order.deadline.replace(tzinfo=None) if isinstance(order.deadline, datetime) else order.deadline
        ])

    # 3-varaq: To'lovlar va pul harakati
    ws = wb.create_sheet("To'lovlar tarixi")
    ws.append([
        "Sana", "Ishchi", "Operatsiya",
        "Summa (so'm)", "Izoh"
    ])

    transactions = list((
        await session.scalars(
            select(BalanceTransaction)
            .order_by(BalanceTransaction.created_at)
        )
    ).all())

    user_names = {u.id: u.full_name for u in users}

    for transaction in transactions:
        ws.append([
            transaction.created_at.replace(tzinfo=None)
                if transaction.created_at else None,
            user_names.get(transaction.user_id, ""),
            transaction.kind,
            float(transaction.amount or 0),
            transaction.note or ""
        ])

    # 4-varaq: Hisob davrlari
    ws = wb.create_sheet("Hisob davrlari")
    ws.append([
        "Ishchi", "Boshlangan sana", "Yakunlangan sana",
        "Hisoblangan daromad (so'm)",
        "Avanslar (so'm)", "Oylik to'lovlari (so'm)",
        "Holati"
    ])

    periods = list((
        await session.scalars(
            select(PayrollPeriod)
            .order_by(PayrollPeriod.created_at)
        )
    ).all())

    for period in periods:
        ws.append([
            user_names.get(period.user_id, ""),
            period.created_at.replace(tzinfo=None)
                if period.created_at else None,
            period.closed_at.replace(tzinfo=None)
                if period.closed_at else None,
            float(period.earned_amount or 0),
            float(period.advance_amount or 0),
            float(period.salary_paid or 0),
            "Yakunlangan" if period.closed else "Ochiq"
        ])

    # Barcha varaqlarni formatlash
    for sheet in wb.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(
                fill_type="solid",
                fgColor="245A81"
            )
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center"
            )

        for column in sheet.columns:
            max_length = 0
            column_letter = column[0].column_letter

            for cell in column:
                value = str(cell.value or "")
                max_length = max(max_length, len(value))

            sheet.column_dimensions[column_letter].width = min(
                max(max_length + 2, 14), 35
            )

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return output.getvalue()

async def get_or_create_payroll_period(session, worker_id):
    # Ishchining ochiq hisob davrini topamiz
    period = await session.scalar(
        select(PayrollPeriod)
        .where(
            PayrollPeriod.user_id == worker_id,
            PayrollPeriod.closed == False
        )
        .order_by(PayrollPeriod.id.desc())
    )

    if period:
        return period

    # Oldin yopilgan davr bo'lsa, undan keyin boshlaymiz
    last_closed_at = await session.scalar(
        select(func.max(PayrollPeriod.closed_at))
        .where(
            PayrollPeriod.user_id == worker_id,
            PayrollPeriod.closed == True
        )
    )

    if last_closed_at:
        start_at = last_closed_at
    else:
        # Birinchi davrda mavjud tranzaksiyalar tarixidan foydalanamiz
        first_transaction = await session.scalar(
            select(func.min(BalanceTransaction.created_at))
            .where(BalanceTransaction.user_id == worker_id)
        )
        start_at = first_transaction or datetime.utcnow()

    period = PayrollPeriod(
        user_id=worker_id,
        year=start_at.year,
        month=start_at.month,
        created_at=start_at,
        closed=False
    )

    session.add(period)
    await session.flush()

    return period

async def admin_required(message, session, super_only=False):
    # Message yoki CallbackQuery bo'lishidan qat'i nazar,
    # foydalanuvchi ID sini to'g'ri olamiz.
    if isinstance(message, CallbackQuery):
        telegram_id = message.from_user.id
        reply_message = message.message
    else:
        telegram_id = message.from_user.id
        reply_message = message

    u = await get_user_by_tg(session, telegram_id)

    allowed = (
        {Role.SUPER_ADMIN}
        if super_only
        else {Role.SUPER_ADMIN, Role.ASSISTANT_ADMIN}
    )

    if not u or u.role not in allowed:
        if isinstance(message, CallbackQuery):
            await message.answer(
                "⛔ Ruxsat yo'q.",
                show_alert=True
            )
        else:
            await reply_message.answer("⛔ Ruxsat yo'q.")

        return None

    return u


async def notify_assigned_worker(
    bot,
    session: AsyncSession,
    order: Order,
    change_text: str
):
    # Buyurtmaga hozir biriktirilgan faol ishchini topamiz
    assignment = await session.scalar(
        select(Assignment)
        .where(
            Assignment.order_id == order.id,
            Assignment.status == AssignmentStatus.ACTIVE
        )
        .order_by(Assignment.id.desc())
        .limit(1)
    )

    if not assignment:
        return "⚠️ Faol biriktirilgan ishchi topilmadi."

    worker = await session.get(User, assignment.user_id)

    if not worker:
        return "⚠️ Biriktirilgan ishchi bazadan topilmadi."

    text = (
        f"🔔 BUYURTMA YANGILANDI\n\n"
        f"📦 Buyurtma №{order.id}: {order.title}\n"
        f"✏️ O'zgartirish: {change_text}\n\n"
        f"📄 Tavsif: {order.description or '-'}\n"
        f"⚡ Ustuvorlik: "
        f"{'🔴 Shoshilinch' if order.priority == Priority.URGENT else '🟢 Oddiy'}\n"
        f"📅 Muddat: "
        f"{order.deadline.strftime('%d.%m.%Y') if isinstance(order.deadline, datetime) else order.deadline or 'Belgilanmagan'}"
    )

    try:
        await bot.send_message(
            chat_id=worker.telegram_id,
            text=text
        )
        return f"📨 Xabar {worker.full_name} ga yuborildi."
    except Exception:
        import logging
        logging.exception(
            "Buyurtma tahriri haqida ishchiga xabar yuborilmadi"
        )
        return (
            f"❌ {worker.full_name} ga xabar yuborilmadi. "
            "Ishchi botni ochib /start bosganini tekshiring."
        )

@router.message(F.text == "📢 Xabar yuborish")
async def broadcast_start(
    message: Message,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(message, session, True):
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="👥 Hammaga",
                callback_data="broadcast:all"
            ),
            InlineKeyboardButton(
                text="👤 Alohida",
                callback_data="broadcast:one"
            )
        ]
    ])

    await message.answer(
        "📢 Xabar yuborish usulini tanlang:",
        reply_markup=kb
    )


@router.callback_query(F.data == "broadcast:all")
async def broadcast_all_start(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    admin = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    if not admin or admin.role != Role.SUPER_ADMIN:
        await callback.answer(
            "⛔ Ruxsat yo'q.",
            show_alert=True
        )
        return

    await state.update_data(target="all")
    await state.set_state(Broadcast.message)

    await callback.message.answer(
        "👥 Hammaga yuborish.\n\n"
        "Yuboriladigan xabarni yuboring:"
    )

    await callback.answer()


@router.callback_query(F.data == "broadcast:one")
async def broadcast_one_start(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    admin = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    if not admin or admin.role != Role.SUPER_ADMIN:
        await callback.answer(
            "⛔ Ruxsat yo'q.",
            show_alert=True
        )
        return

    users = list(
        (
            await session.scalars(
                select(User)
                .where(User.is_active == True)
                .order_by(User.full_name)
            )
        ).all()
    )

    if not users:
        await callback.answer(
            "Foydalanuvchilar topilmadi.",
            show_alert=True
        )
        return

    buttons = []

    for u in users:
        if u.username:
            name = f"@{u.username}"
        else:
            name = u.full_name

        buttons.append([
            InlineKeyboardButton(
                text=f"👤 {name}",
                callback_data=f"broadcast:user:{u.id}"
            )
        ])

    await callback.message.answer(
        "👤 Xabar yuboriladigan foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


@router.callback_query(F.data.startswith("broadcast:user:"))
async def broadcast_user_select(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    admin = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    if not admin or admin.role != Role.SUPER_ADMIN:
        await callback.answer(
            "⛔ Ruxsat yo'q.",
            show_alert=True
        )
        return

    uid = int(callback.data.split(":")[2])

    user = await session.get(User, uid)

    if not user:
        await callback.answer(
            "Foydalanuvchi topilmadi.",
            show_alert=True
        )
        return

    await state.update_data(
        target="one",
        user_id=user.id
    )

    await state.set_state(Broadcast.message)

    if user.username:
        name = f"@{user.username}"
    else:
        name = user.full_name

    await callback.message.answer(
        f"👤 Tanlandi: {name}\n\n"
        "📨 Yuboriladigan xabarni yuboring:"
    )

    await callback.answer()


@router.message(Broadcast.message)
async def broadcast_send(
    message: Message,
    session: AsyncSession,
    state: FSMContext
):
    admin = await admin_required(message, session, True)

    if not admin:
        return

    data = await state.get_data()
    target = data.get("target")

    sent = 0
    failed = 0

    if target == "one":

        user = await session.get(
            User,
            data.get("user_id")
        )

        if not user:
            await state.clear()
            await message.answer(
                "❌ Foydalanuvchi topilmadi."
            )
            return

        try:
            await message.bot.copy_message(
                chat_id=user.telegram_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            sent = 1

        except Exception:
            failed = 1

    elif target == "all":

        users = list(
            (
                await session.scalars(
                    select(User)
                    .where(User.is_active == True)
                )
            ).all()
        )

        for user in users:

            try:
                await message.bot.copy_message(
                    chat_id=user.telegram_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id
                )

                sent += 1

            except Exception:
                failed += 1

    await state.clear()

    await message.answer(
        f"✅ Xabar yuborish tugadi.\n\n"
        f"📨 Yuborildi: {sent} ta\n"
        f"❌ Yuborilmadi: {failed} ta"
    )    

@router.message(F.text == "👥 Foydalanuvchilar")
async def users(
    message: Message,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(message, session, True):
        return

    workers = list(
        (
            await session.scalars(
                select(User)
                .where(
                    User.is_active == True,
                    User.role.in_([
                        Role.WELDER,
                        Role.PAINTER,
                        Role.COURIER,
                        Role.AKFA
                    ])
                )
                .order_by(User.full_name)
            )
        ).all()
    )

    buttons = []

    for u in workers:
        if u.username:
            name = f"@{u.username}"
        else:
            name = u.full_name

        role_names = {
            Role.WELDER: "👨‍🏭 Payvandchi",
            Role.PAINTER: "🎨 Bo'yoqchi",
            Role.COURIER: "🚚 Kuryer",
            Role.AKFA: "🪟 Akfachi"
        }

        role_name = role_names.get(u.role, u.role.value)

        buttons.append([
            InlineKeyboardButton(
                text=f"{role_name} — {name}",
                callback_data=f"userrole:{u.id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="➕ Yangi rol berish",
            callback_data="newrole"
        )
    ])

    if workers:
        text = "👥 Ishchilar ro'yxati:"
    else:
        text = "👥 Hozircha ishchilar yo'q."

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


@router.callback_query(F.data.startswith("userrole:"))
async def user_role_select(
    callback: CallbackQuery,
    session: AsyncSession
):
    admin = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    if not admin or admin.role != Role.SUPER_ADMIN:
        await callback.answer(
            "⛔ Ruxsat yo'q.",
            show_alert=True
        )
        return

    uid = int(callback.data.split(":")[1])

    user = await session.get(User, uid)

    if not user:
        await callback.answer(
            "Foydalanuvchi topilmadi.",
            show_alert=True
        )
        return

    if user.username:
        name = f"@{user.username}"
    else:
        name = user.full_name

    await callback.message.answer(
        f"👤 Foydalanuvchi: {name}\n"
        f"📌 Hozirgi roli: {user.role.value}\n\n"
        f"Yangi rolni tanlang:",
        reply_markup=role_kb(user.id)
    )

    await callback.answer()


@router.callback_query(F.data == "newrole")
async def new_role_start(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    admin = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    if not admin or admin.role != Role.SUPER_ADMIN:
        await callback.answer(
            "⛔ Ruxsat yo'q.",
            show_alert=True
        )
        return

    users = list(
        (
            await session.scalars(
                select(User)
                .where(
                    User.is_active == True,
                    User.role == Role.USER
                )
                .order_by(User.full_name)
            )
        ).all()
    )

    if not users:
        await callback.message.answer(
            "ℹ️ Rol berish uchun yangi foydalanuvchi yo'q.\n\n"
            "Foydalanuvchi avval botga /start bosishi kerak."
        )
        await callback.answer()
        return

    buttons = []

    for user in users:
        name = user.full_name or "Ismi ko'rsatilmagan"

        if user.username:
            name += f" (@{user.username})"

        buttons.append([
            InlineKeyboardButton(
                text=f"👤 {name}",
                callback_data=f"newroleuser:{user.id}"
            )
        ])

    await callback.message.answer(
        f"➕ Rol berish uchun foydalanuvchini tanlang:\n\n"
        f"👥 Ro'yxatda: {len(users)} ta",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()
    admin = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    if not admin or admin.role != Role.SUPER_ADMIN:
        await callback.answer(
            "⛔ Ruxsat yo'q.",
            show_alert=True
        )
        return


@router.callback_query(F.data.startswith("newroleuser:"))
async def choose_user(
    callback: CallbackQuery,
    session: AsyncSession
):
    admin = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    if not admin or admin.role != Role.SUPER_ADMIN:
        await callback.answer(
            "⛔ Ruxsat yo'q.",
            show_alert=True
        )
        return

    uid = int(callback.data.split(":")[1])
    user = await session.get(User, uid)

    if not user or not user.is_active:
        await callback.answer(
            "❌ Foydalanuvchi topilmadi yoki faol emas.",
            show_alert=True
        )
        return

    if user.role != Role.USER:
        await callback.answer(
            "ℹ️ Bu foydalanuvchiga allaqachon rol berilgan.",
            show_alert=True
        )
        return

    name = user.full_name or "Ismi ko'rsatilmagan"

    if user.username:
        name += f" (@{user.username})"

    await callback.message.answer(
        f"👤 Foydalanuvchi: {name}\n"
        f"📌 Hozirgi rol: Oddiy foydalanuvchi\n\n"
        f"Yangi rolni tanlang:",
        reply_markup=role_kb(user.id)
    )

    await callback.answer()

    if not await admin_required(message, session, True):
        return

    if not message.text.isdigit():
        await message.answer(
            "❌ Faqat Telegram ID raqamini yuboring."
        )
        return

    u = await session.scalar(
        select(User).where(
            User.telegram_id == int(message.text)
        )
    )

    if not u:
        await message.answer(
            "❌ Foydalanuvchi topilmadi.\n\n"
            "U botga /start bosgan bo'lishi kerak."
        )
        return

    await state.clear()

    if u.username:
        name = f"@{u.username}"
    else:
        name = u.full_name

    await message.answer(
        f"👤 Foydalanuvchi: {name}\n"
        f"📌 Hozirgi rol: {u.role.value}\n\n"
        f"Yangi rolni tanlang:",
        reply_markup=role_kb(u.id)
    )

@router.callback_query(F.data.startswith("setrole:"))
async def set_role(
    callback: CallbackQuery,
    session: AsyncSession
):
    admin = await get_user_by_tg(
        session,
        callback.from_user.id
    )

    if not admin or admin.role != Role.SUPER_ADMIN:
        await callback.answer(
            "⛔ Ruxsat yo'q.",
            show_alert=True
        )
        return

    _, uid, role = callback.data.split(":")

    target = await session.get(User, int(uid))

    if not target:
        await callback.answer(
            "❌ Foydalanuvchi topilmadi.",
            show_alert=True
        )
        return

    if target.role == Role.SUPER_ADMIN:
        await callback.answer(
            "Bosh admin rolini o'zgartirib bo'lmaydi.",
            show_alert=True
        )
        return

    target.role = Role(role)
    await session.commit()

    # Rol nomini o'zbekcha ko'rsatish
    role_names = {
        Role.SUPER_ADMIN: "Bosh admin",
        Role.ASSISTANT_ADMIN: "Yordamchi admin",
        Role.WELDER: "Payvandchi",
        Role.PAINTER: "Bo'yoqchi",
        Role.COURIER: "Kuryer",
        Role.AKFA: "Akfachi",
        Role.USER: "Oddiy foydalanuvchi"
    }

    role_name = role_names.get(
        target.role,
        target.role.value
    )

    # Foydalanuvchining o'ziga xabar yuborish
    try:
        await callback.bot.send_message(
            chat_id=target.telegram_id,
            text=(
                "🎉 Sizga yangi rol berildi!\n\n"
                f"👤 Sizning rolingiz: {role_name}\n\n"
                "✅ Endi botdan o'z rolingizga mos "
                "ravishda foydalanishingiz mumkin."
            )
        )
    except Exception:
        pass

    await callback.answer(
        "✅ Rol saqlandi!",
        show_alert=True
    )

    await callback.message.edit_text(
        f"✅ {target.full_name}\n"
        f"Yangi rol: {role_name}"
    )


def order_preview_text(data):
    deadline = data.get("deadline")
    if isinstance(deadline, datetime):
        deadline_text = deadline.strftime("%d.%m.%Y")
    else:
        deadline_text = deadline or "Belgilanmagan"

    priority = (
        "🔴 Shoshilinch"
        if data.get("priority") == "urgent"
        else "🟢 Oddiy"
    )

    photos = data.get("media", [])

    return (
        "📋 BUYURTMA — TASDIQLASH\n\n"
        f"📦 Nomi: {data.get('title') or '-'}\n"
        f"📄 Tavsifi: {data.get('description') or '-'}\n"
        f"👤 Mijoz: {data.get('customer_name') or '-'}\n"
        f"📞 Telefon: {data.get('customer_phone') or '-'}\n"
        f"📍 Manzil: {data.get('customer_address') or '-'}\n"
        f"💰 Narxi: {money(data.get('price', 0))} so'm\n"
        f"📅 Muddat: {deadline_text}\n"
        f"⚡ Ustuvorlik: {priority}\n"
        f"🖼 Rasmlar: {len(photos)} ta\n\n"
        "Ma'lumotlarni tekshiring."
    )


def order_review_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Buyurtmani qo'shish",
                callback_data="create_order:confirm"
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Tahrirlash",
                callback_data="create_order:edit"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Bekor qilish",
                callback_data="create_order:cancel"
            )
        ]
    ])



async def show_order_preview(message, state):
    data = await state.get_data()
    await state.set_state(CreateOrder.review)

    preview_message_id = data.get("preview_message_id")
    text = order_preview_text(data)
    keyboard = order_review_keyboard()

    if preview_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=preview_message_id,
                text=text,
                reply_markup=keyboard
            )
            return
        except Exception:
            pass

    sent = await message.answer(
        text,
        reply_markup=keyboard
    )
    await state.update_data(preview_message_id=sent.message_id)



@router.message(F.text == "➕ Buyurtma yaratish")
async def c1(message: Message, session: AsyncSession, state: FSMContext):
    if not await admin_required(message, session):
        return

    await state.clear()

    # Eski pastki klaviaturani yashirish
    await message.answer(
        "Buyurtma yaratish boshlandi.",
        reply_markup=ReplyKeyboardRemove()
    )

    await state.set_state(CreateOrder.title)

    await message.answer("1/9 Buyurtma nomi:")


@router.message(CreateOrder.title)
async def c2(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(CreateOrder.description)
    await message.answer("2/9 Tavsif/o'lcham:")


@router.message(CreateOrder.description)
async def c3(message: Message, state: FSMContext):
    await state.update_data(description=message.text.strip())
    await state.set_state(CreateOrder.customer_name)
    await message.answer("3/9 Mijoz ismi:")


@router.message(CreateOrder.customer_name)
async def c4(message: Message, state: FSMContext):
    await state.update_data(customer_name=message.text.strip())
    await state.set_state(CreateOrder.customer_phone)
    await message.answer("4/9 Telefon:")


@router.message(CreateOrder.customer_phone)
async def c5(message: Message, state: FSMContext):
    await state.update_data(customer_phone=message.text.strip())
    await state.set_state(CreateOrder.customer_address)
    await message.answer("5/9 Manzil/lokatsiya matni:")


@router.message(CreateOrder.customer_address)
async def c6(message: Message, state: FSMContext):
    await state.update_data(customer_address=message.text.strip())
    await state.set_state(CreateOrder.price)
    await message.answer("6/9 Narx:")


@router.message(CreateOrder.price)
async def c7(message: Message, state: FSMContext):
    try:
        price = parse_money(message.text)
        if price <= 0:
            raise ValueError
    except (ValueError, TypeError, ArithmeticError):
        await message.answer("❌ Narxni to'g'ri kiriting. Masalan: 2500000")
        return

    await state.update_data(price=price, deadline=None)
    await state.set_state(CreateOrder.deadline)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⏭ Muddatni o'tkazib yuborish",
                callback_data="create_deadline:skip"
            )
        ]
    ])

    await message.answer(
        "7/9 Buyurtma muddatini kiriting (KK.OO.YYYY).\n"
        "Masalan: 25.10.2026\n"
        "Muddat shart emas — o'tkazib yuborish tugmasini bosing.",
        reply_markup=keyboard
    )


@router.callback_query(F.data == "create_deadline:skip")
async def create_deadline_skip(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    current_state = await state.get_state()
    if current_state != CreateOrder.deadline.state:
        await callback.answer("Bu amal hozir mavjud emas.", show_alert=True)
        return

    await state.update_data(deadline=None)
    await state.set_state(CreateOrder.priority)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🟢 Oddiy",
                callback_data="create_priority:normal"
            ),
            InlineKeyboardButton(
                text="🔴 Shoshilinch",
                callback_data="create_priority:urgent"
            )
        ]
    ])

    await callback.message.answer(
        "8/9 Buyurtma ustuvorligini tanlang:",
        reply_markup=keyboard
    )
    await callback.answer()


@router.message(CreateOrder.deadline)
async def c8(message: Message, state: FSMContext):
    try:
        deadline = parse_deadline(message.text)
    except (ValueError, TypeError):
        await message.answer(
            "❌ Sana noto'g'ri.\n"
            "Masalan: 25.10.2026 yoki muddatni o'tkazib yuborish tugmasini bosing."
        )
        return

    await state.update_data(deadline=deadline)
    await state.set_state(CreateOrder.priority)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🟢 Oddiy",
                callback_data="create_priority:normal"
            ),
            InlineKeyboardButton(
                text="🔴 Shoshilinch",
                callback_data="create_priority:urgent"
            )
        ]
    ])

    await message.answer(
        "8/9 Buyurtma ustuvorligini tanlang:",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("create_priority:"))
async def create_priority_select(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    if await state.get_state() != CreateOrder.priority.state:
        await callback.answer("Bu amal hozir mavjud emas.", show_alert=True)
        return

    priority = callback.data.split(":")[1]
    if priority not in {"normal", "urgent"}:
        await callback.answer("Ustuvorlik noto'g'ri.", show_alert=True)
        return

    await state.update_data(priority=priority, media=[])
    await state.set_state(CreateOrder.media)

    await callback.message.answer(
        "9/9 10 tagacha rasm yuboring.\n"
        "Rasmlar bo'lmasa ham, tugatish uchun /tayyor yuboring."
    )
    await callback.answer()






@router.message(CreateOrder.media, F.photo)
async def cm(message: Message, state: FSMContext):
    data = await state.get_data()
    media = data.get("media", [])

    if len(media) >= 10:
        await message.answer("Maksimum 10 ta rasm yuborish mumkin.")
        return

    media.append(message.photo[-1].file_id)
    await state.update_data(media=media)

    await message.answer(
        f"✅ Rasm saqlandi ({len(media)}/10). "
        "Yana rasm yuboring yoki /tayyor yozing."
    )



@router.message(CreateOrder.media, F.text == "/tayyor")
async def create_order_media_done(
    message: Message,
    state: FSMContext
):
    await show_order_preview(message, state)



    # Bu yerda tasdiqlash oynasi chaqirilmaydi.
    # Rasmlar bitta buyurtma uchun yig'iladi.



@router.callback_query(F.data == "create_order:confirm")
async def create_order_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    if await state.get_state() != CreateOrder.review.state:
        await callback.answer("Tasdiqlash oynasi eskirgan.", show_alert=True)
        return

    data = await state.get_data()
    admin = await get_user_by_tg(session, callback.from_user.id)

    if not admin:
        await callback.answer("Admin topilmadi.", show_alert=True)
        return

    payload = {
        key: data.get(key)
        for key in [
            "title", "description", "customer_name",
            "customer_phone", "customer_address",
            "price", "deadline"
        ]
    }
    payload["priority"] = Priority(data["priority"])

    order = await create_order(
        session, payload, data.get("media", []), admin
    )

    await state.clear()
    await callback.message.answer(
        f"✅ Buyurtma №{order.id} muvaffaqiyatli yaratildi.",
        reply_markup=main_menu(admin.role)
    )
    await callback.answer("Buyurtma saqlandi!")


@router.callback_query(F.data == "create_order:cancel")
async def create_order_cancel(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    await state.clear()
    await callback.message.answer("❌ Buyurtma yaratish bekor qilindi.")
    await callback.answer()


@router.callback_query(F.data == "create_order:edit")
async def create_order_edit_menu(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    if await state.get_state() != CreateOrder.review.state:
        await callback.answer("Tahrirlash oynasi eskirgan.", show_alert=True)
        return

    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Nomi", callback_data="create_edit:title")],
        [InlineKeyboardButton(text="📄 Tavsifi", callback_data="create_edit:description")],
        [InlineKeyboardButton(text="👤 Mijoz ismi", callback_data="create_edit:customer_name")],
        [InlineKeyboardButton(text="📞 Telefon", callback_data="create_edit:customer_phone")],
        [InlineKeyboardButton(text="📍 Manzil", callback_data="create_edit:customer_address")],
        [InlineKeyboardButton(text="💰 Narxi", callback_data="create_edit:price")],
        [InlineKeyboardButton(text="📅 Muddat", callback_data="create_edit:deadline")],
        [InlineKeyboardButton(text="⚡ Ustuvorlik", callback_data="create_edit:priority")],
        [InlineKeyboardButton(text="🖼 Rasmlarni o'zgartirish", callback_data="create_edit:media")],
        [InlineKeyboardButton(text="⬅️ Buyurtmaga qaytish", callback_data="create_edit:back")],
    ])
    await callback.message.answer(
        "Qaysi ma'lumotni o'zgartirmoqchisiz?",
        reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data.startswith("create_edit:"))
async def create_order_edit_field(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    field = callback.data.split(":")[1]

    if field == "back":
        await show_order_preview(callback.message, state)
        await callback.answer()
        return

    if field == "priority":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟢 Oddiy",
                    callback_data="create_edit_priority:normal"
                ),
                InlineKeyboardButton(
                    text="🔴 Shoshilinch",
                    callback_data="create_edit_priority:urgent"
                )
            ]
        ])
        await callback.message.answer(
            "Yangi ustuvorlikni tanlang:",
            reply_markup=keyboard
        )
        await callback.answer()
        return

    if field not in {
        "title", "description", "customer_name",
        "customer_phone", "customer_address", "price", "deadline"
    }:
        await callback.answer("Maydon noto'g'ri.", show_alert=True)
        return

    await state.update_data(edit_field=field)
    await state.set_state(CreateOrder.edit_value)

    prompts = {
        "title": "Yangi buyurtma nomini yuboring:",
        "description": "Yangi tavsifni yuboring:",
        "customer_name": "Yangi mijoz ismini yuboring:",
        "customer_phone": "Yangi telefon raqamini yuboring:",
        "customer_address": "Yangi manzilni yuboring:",
        "price": "Yangi narxni yuboring:",
        "deadline": "Yangi muddatni KK.OO.YYYY formatida yuboring yoki '-' yozing:"
    }

    await callback.message.answer(prompts[field])
    await callback.answer()


@router.callback_query(F.data.startswith("create_edit_priority:"))
async def create_order_edit_priority(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    if await state.get_state() != CreateOrder.review.state:
        await callback.answer("Tahrirlash oynasi eskirgan.", show_alert=True)
        return

    priority = callback.data.split(":")[1]
    if priority not in {"normal", "urgent"}:
        await callback.answer("Ustuvorlik noto'g'ri.", show_alert=True)
        return

    await state.update_data(priority=priority)
    await show_order_preview(callback.message, state)
    await callback.answer("Ustuvorlik yangilandi.")



@router.message(CreateOrder.edit_value, F.text)
async def create_order_edit_value(
    message: Message,
    state: FSMContext
):
    data = await state.get_data()
    field = data.get("edit_field")
    value = message.text.strip()

    if not value:
        await message.answer("Qiymat bo'sh bo'lmasligi kerak.")
        return

    if field == "price":
        try:
            value = parse_money(value)
            if value <= 0:
                raise ValueError
        except (ValueError, TypeError, ArithmeticError):
            await message.answer(
                "Narxni to'g'ri kiriting. Masalan: 2500000"
            )
            return

    elif field == "deadline":
        if value == "-":
            value = None
        else:
            try:
                value = parse_deadline(value)
            except (ValueError, TypeError):
                await message.answer(
                    "❌ Sana noto'g'ri. "
                    "KK.OO.YYYY formatida kiriting, "
                    "masalan: 25.10.2026 yoki '-' yuboring."
                )
                return

    await state.update_data(**{field: value})
    await show_order_preview(message, state)

@router.message(F.text == "⚙️ Foizlar")
async def ps(message: Message, session: AsyncSession, state: FSMContext):
    if not await admin_required(message, session, True): return
    s = await get_or_create_settings(session)
    await state.set_state(SetPercent.values)
    await message.answer(f"Hozir: {s.welder_percent}% / {s.painter_percent}% / {s.courier_percent}%\nYangi: masalan 12 5 5")

@router.message(SetPercent.values)
async def pv(message: Message, session: AsyncSession, state: FSMContext):
    if not await admin_required(message, session, True): return
    try:
        a,b,c = [Decimal(x) for x in message.text.split()]
        if any(x < 0 or x > 100 for x in (a,b,c)): raise ValueError
    except:
        await message.answer("Format: 12 5 5"); return
    s = await get_or_create_settings(session)
    s.welder_percent, s.painter_percent, s.courier_percent = a,b,c
    await session.commit()
    await state.clear()
    await message.answer("✅ Foizlar yangilandi.")


@router.message(F.text == "💳 Balanslar")
async def balances(message: Message, session: AsyncSession):
    if not await admin_required(message, session, True):
        return

    users = list(
        (
            await session.scalars(
                select(User).where(
                    User.role.in_([
                        Role.WELDER,
                        Role.PAINTER,
                        Role.COURIER,
                        Role.AKFA
                    ])
                ).order_by(User.full_name)
            )
        ).all()
    )

    buttons = []

    for u in users:
        buttons.append([
            InlineKeyboardButton(
                text=f"{u.full_name} | {money(u.balance)}",
                callback_data=f"balworker:{u.id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="📊 Hisobotlar",
            callback_data="balances:excel"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        "💳 Ishchilar balanslari\n\n"
        "To'lov qilish uchun ishchini tanlang:",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("balworker:"))
async def balance_worker(callback: CallbackQuery, session: AsyncSession):
    if not await admin_required(callback, session, True):
        await callback.answer()
        return

    worker_id = int(callback.data.split(":")[1])

    worker = await session.get(User, worker_id)

    if not worker:
        await callback.answer("Ishchi topilmadi.", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💵 Avans",
                callback_data=f"paytype:{worker.id}:advance"
            ),
            InlineKeyboardButton(
                text="💰 Oylik",
                callback_data=f"paytype:{worker.id}:salary"
            )
        ],
        [
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="balances:back"
            )
        ]
    ])

    await callback.message.answer(
        f"👤 Ishchi: {worker.full_name}\n"
        f"💳 Joriy balans: {money(worker.balance)}\n\n"
        "Qanday to'lov qilasiz?",
        reply_markup=keyboard
    )
    await callback.answer()

    
@router.callback_query(F.data == "balances:back")
async def balances_back(
    callback: CallbackQuery,
    session: AsyncSession
):
    if not await admin_required(callback, session, True):
        await callback.answer()
        return

    users = list(
        (
            await session.scalars(
                select(User).where(
                    User.role.in_([
                        Role.WELDER,
                        Role.PAINTER,
                        Role.COURIER,
                        Role.AKFA
                    ])
                ).order_by(User.full_name)
            )
        ).all()
    )

    buttons = [
        [
            InlineKeyboardButton(
                text=f"{u.full_name} | {money(u.balance)}",
                callback_data=f"balworker:{u.id}"
            )
        ]
        for u in users
    ]

    buttons.append([
        InlineKeyboardButton(
            text="📊 Hisobotlar",
            callback_data="balances:excel"
        )
    ])

    await callback.message.answer(
        "💳 Ishchilar balanslari\n\nTo'lov uchun ishchini tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("paytype:"))
async def choose_payment_type(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session, True):
        await callback.answer()
        return

    _, worker_id, payment_type = callback.data.split(":")
    worker = await session.get(User, int(worker_id))

    if not worker:
        await callback.answer("Ishchi topilmadi.", show_alert=True)
        return

    await state.update_data(
        worker_id=worker.id,
        payment_type=payment_type
    )
    await state.set_state(WorkerPayment.amount)

    payment_name = "avans" if payment_type == "advance" else "oylik"

    await callback.message.answer(
        f"👤 {worker.full_name}\n"
        f"💳 Joriy balans: {money(worker.balance)}\n\n"
        f"{payment_name.capitalize()} summasini kiriting (so'm):"
    )
    await callback.answer()




@router.message(WorkerPayment.amount)
async def enter_worker_payment(
    message: Message,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(message, session, True):
        await state.clear()
        return

    data = await state.get_data()
    worker = await session.get(User, data["worker_id"])

    if not worker:
        await message.answer("Ishchi topilmadi.")
        await state.clear()
        return

    try:
        amount = Decimal(str(parse_money(message.text)))
    except (ValueError, TypeError, ArithmeticError):
        await message.answer(
            "❌ Summani to'g'ri kiriting. Masalan: 150000"
        )
        return

    if amount <= 0:
        await message.answer(
            "❌ Summa noldan katta bo'lishi kerak."
        )
        return

    old_balance = Decimal(str(worker.balance or 0))

    if amount > old_balance:
        await message.answer(
            f"❌ To'lov balansdan ko'p.\n"
            f"Joriy balans: {money(old_balance)}"
        )
        return

    payment_type = data["payment_type"]
    payment_name = "Avans" if payment_type == "advance" else "Oylik"

    period = await get_or_create_payroll_period(
        session,
        worker.id
    )

    # To'lovdan oldingi va keyingi balans
    new_balance = old_balance - amount
    worker.balance = new_balance

    session.add(
        BalanceTransaction(
            user_id=worker.id,
            amount=-amount,
            kind=payment_type,
            note=f"{payment_name} to'lovi"
        )
    )

    await session.commit()
    await state.clear()

    # Adminga ko'rsatiladigan xabar
    text = (
        f"✅ To'lov saqlandi!\n\n"
        f"👤 Ishchi: {worker.full_name}\n"
        f"💳 Oldingi balans: {money(old_balance)}\n"
        f"💵 {payment_name}: {money(amount)}\n"
        f"💰 Qolgan balans: {money(new_balance)}"
    )

    # Ishchiga yuboriladigan xabar
    worker_text = (
        f"💰 Sizga {payment_name.lower()} berildi!\n\n"
        f"💳 To'lovdan oldingi balans: {money(old_balance)}\n"
        f"💵 Berilgan {payment_name.lower()}: {money(amount)}\n"
        f"💰 Qolgan balans: {money(new_balance)}"
    )

    try:
        await message.bot.send_message(
            chat_id=worker.telegram_id,
            text=worker_text
        )
    except Exception:
        await message.answer(
            "⚠️ Ishchiga xabar yuborilmadi. "
            "U botni ochganini va botni bloklamaganini tekshiring."
        )

    if payment_type == "salary":
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Ha, yakunlash",
                        callback_data=f"payroll:close:{period.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Yo'q, davom etish",
                        callback_data=f"payroll:keep:{period.id}"
                    )
                ]
            ]
        )

        text += (
            "\n\n📅 Bu hisob davri yakunlansinmi?\n"
            "Yakunlansa, yangi hisob davri boshlanadi."
        )

        await message.answer(
            text,
            reply_markup=keyboard
        )
    else:
        await message.answer(text)

        
@router.callback_query(F.data.startswith("payroll:keep:"))
async def payroll_keep(
    callback: CallbackQuery,
    session: AsyncSession
):
    if not await admin_required(callback, session, True):
        await callback.answer()
        return

    period_id = int(callback.data.split(":")[2])
    period = await session.get(PayrollPeriod, period_id)

    if not period:
        await callback.answer(
            "Hisob davri topilmadi.",
            show_alert=True
        )
        return

    if period.closed:
        await callback.answer(
            "Bu davr allaqachon yakunlangan.",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        "✅ Hisob davri ochiq qoldirildi.\n"
        "Keyingi to'lovlarni shu davrda davom ettirishingiz mumkin."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payroll:close:"))
async def payroll_close(
    callback: CallbackQuery,
    session: AsyncSession
):
    if not await admin_required(callback, session, True):
        await callback.answer()
        return

    period_id = int(callback.data.split(":")[2])
    period = await session.get(PayrollPeriod, period_id)

    if not period:
        await callback.answer(
            "Hisob davri topilmadi.",
            show_alert=True
        )
        return

    if period.closed:
        await callback.answer(
            "Bu hisob davri oldin yakunlangan.",
            show_alert=True
        )
        return

    now = datetime.utcnow()
    worker = await session.get(User, period.user_id)

    if not worker:
        await callback.answer(
            "Ishchi topilmadi.",
            show_alert=True
        )
        return

    # Faqat ushbu davrdagi daromadlar va to'lovlarni hisoblaymiz
    base_filter = (
        BalanceTransaction.user_id == period.user_id,
        BalanceTransaction.created_at >= period.created_at,
        BalanceTransaction.created_at <= now
    )

    earned = await session.scalar(
        select(func.coalesce(func.sum(BalanceTransaction.amount), 0))
        .where(
            *base_filter,
            BalanceTransaction.kind.in_(["earn", "revert"])
        )
    )

    advances = await session.scalar(
        select(func.coalesce(func.sum(BalanceTransaction.amount), 0))
        .where(
            *base_filter,
            BalanceTransaction.kind == "advance"
        )
    )

    salaries = await session.scalar(
        select(func.coalesce(func.sum(BalanceTransaction.amount), 0))
        .where(
            *base_filter,
            BalanceTransaction.kind == "salary"
        )
    )

    # Daromadlar va to'lovlar tarixini davrga saqlaymiz
    period.earned_amount = Decimal(str(earned or 0))
    period.advance_amount = -Decimal(str(advances or 0))
    period.salary_paid = -Decimal(str(salaries or 0))
    period.closed = True
    period.closed_at = now

    # Yangi hisob davrini ochamiz.
    # Ishchining balansiga tegmaymiz!
    new_period = PayrollPeriod(
        user_id=worker.id,
        year=now.year,
        month=now.month,
        created_at=now,
        closed=False
    )

    session.add(new_period)
    await session.commit()

    await callback.message.edit_text(
        f"✅ Hisob davri yakunlandi!\n\n"
        f"👤 Ishchi: {worker.full_name}\n"
        f"📅 Davr: {period.created_at:%d.%m.%Y} — "
        f"{now:%d.%m.%Y}\n"
        f"💰 Hisoblangan daromad: "
        f"{money(period.earned_amount)}\n"
        f"💵 Avanslar: {money(period.advance_amount)}\n"
        f"💰 Oylik to'lovlari: {money(period.salary_paid)}\n\n"
        f"💳 Hozirgi qolgan balans: {money(worker.balance)}\n\n"
        f"🆕 Yangi hisob davri boshlandi."
    )

    await callback.answer("Hisob davri yakunlandi.")
@router.message(F.text.startswith("/revert "))
async def rev(message: Message, session: AsyncSession):
    if not await admin_required(message, session, True): return
    try:
        _, oid, role = message.text.split()
        role = Role(role)
    except:
        await message.answer("/revert ORDER_ID welder|painter|courier"); return
    ok, msg = await revert_stage(session, int(oid), role)
    await message.answer(("✅ " if ok else "❌ ") + msg)


@router.message(F.text == "🔄 Ish jarayonidagi buyurtmalar")
async def active_orders(
    message: Message,
    session: AsyncSession
):
    if not await admin_required(message, session):
        return

    # Tugallanmagan buyurtmalarning barcha bosqichlari
    in_progress_statuses = [
        OrderStatus.WELDING,
        OrderStatus.WAITING_PAINT,
        OrderStatus.PAINTING,
        OrderStatus.WAITING_COURIER,
        OrderStatus.DELIVERY,
    ]

    # Hozir faol topshiriqni ham topamiz.
    # Faol topshiriq bo'lmasa ham buyurtma ro'yxatda ko'rinadi.
    result = await session.execute(
        select(Order, Assignment, User)
        .outerjoin(
            Assignment,
            (Assignment.order_id == Order.id)
            & (Assignment.status == AssignmentStatus.ACTIVE)
        )
        .outerjoin(
            User,
            User.id == Assignment.user_id
        )
        .where(Order.status.in_(in_progress_statuses))
        .order_by(Order.created_at.desc())
    )

    items = result.all()

    if not items:
        await message.answer(
            "🔄 Hozir ish jarayonida buyurtmalar yo'q."
        )
        return

    role_names = {
        Role.WELDER: "👨‍🏭 Payvandchi",
        Role.PAINTER: "🎨 Bo'yoqchi",
        Role.COURIER: "🚚 Kuryer",
        Role.AKFA: "🪟 Akfachi",
    }

    status_names = {
        OrderStatus.WELDING: "🔧 Payvandlash",
        OrderStatus.WAITING_PAINT: "⏳ Bo'yoqchini kutmoqda",
        OrderStatus.PAINTING: "🎨 Bo'yash",
        OrderStatus.WAITING_COURIER: "⏳ Kuryerni kutmoqda",
        OrderStatus.DELIVERY: "🚚 Yetkazib berish",
    }

    texts = []

    for order, assignment, user in items:
        status_name = status_names.get(
            order.status,
            order.status.value
        )

        if assignment and user:
            role_name = role_names.get(
                assignment.role,
                assignment.role.value
            )
            worker_name = user.full_name

            if user.username:
                worker_name += f" (@{user.username})"

            worker_text = f"{role_name}: {worker_name}"
        else:
            worker_text = "👷 Hozircha faol topshiriq yo'q"

        deadline = (
            order.deadline.strftime("%d.%m.%Y")
            if order.deadline
            else "Belgilanmagan"
        )

        priority = (
            "🔥 Shoshilinch"
            if order.priority == Priority.URGENT
            else "Oddiy"
        )

        texts.append(
            f"📦 <b>#{order.id} — {order.title}</b>\n"
            f"💰 Narx: {money(order.price)}\n"
            f"📌 Bosqich: {status_name}\n"
            f"{worker_text}\n"
            f"📅 Muddat: {deadline}\n"
            f"⚡ {priority}"
        )

    # Telegram xabar uzunligi chegarasidan oshmaslik uchun
    # ro'yxatni bir nechta xabarga bo'lamiz.
    header = f"🔄 <b>Ish jarayonidagi buyurtmalar: {len(items)} ta</b>\n\n"
    current_text = header

    for item_text in texts:
        part = item_text + "\n\n━━━━━━━━━━━━━━\n\n"

        if len(current_text) + len(part) > 3500:
            await message.answer(
                current_text,
                parse_mode="HTML"
            )
            current_text = header + item_text + "\n\n━━━━━━━━━━━━━━\n\n"
        else:
            current_text += part

    if current_text.strip() != header.strip():
        await message.answer(
            current_text,
            parse_mode="HTML"
        )



@router.message(F.text == "📋 Buyurtmalar")
async def orders(message: Message, session: AsyncSession):
    if not await admin_required(message, session):
        return

    items = list(
        (
            await session.scalars(
                select(Order)
                .order_by(Order.created_at.desc())
                .limit(30)
            )
        ).all()
    )

    if not items:
        await message.answer("Buyurtmalar yo'q.")
        return

    buttons = []

    for o in items:
        buttons.append([
            InlineKeyboardButton(
                text=f"📦 #{o.id} | {o.title} | {money(o.price)} so'm",
                callback_data=f"order_detail:{o.id}"
            )
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        "📋 Buyurtmalar ro'yxati:\n\n"
        "Batafsil ma'lumot olish uchun buyurtmani tanlang:",
        reply_markup=keyboard
    )



@router.callback_query(F.data.startswith("order_detail:"))
async def order_detail(callback: CallbackQuery, session: AsyncSession):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    order_id = int(callback.data.split(":")[1])
    order = await session.get(Order, order_id)

    if not order:
        await callback.answer("Buyurtma topilmadi!", show_alert=True)
        return

    text = (
        f"📦 BUYURTMA №{order.id} — {order.title}\n\n"
        f"📄 Tavsifi: {order.description or '-'}\n"
        f"👤 Mijoz: {order.customer_name or '-'}\n"
        f"📞 Telefon: {order.customer_phone or '-'}\n"
        f"📍 Manzil: {order.customer_address or '-'}\n"
        f"💰 Summa: {money(order.price)} so'm\n"
        f"📊 Holati: {order.status.value if order.status else '-'}\n"
        f"⚡ Ustuvorligi: {order.priority.value if order.priority else '-'}\n"
        f"📅 Muddat: {order.deadline or '-'}\n"
        f"🕒 Yaratilgan: "
        f"{order.created_at.strftime('%d.%m.%Y %H:%M') if order.created_at else '-'}"
    )

    result = await session.scalars(
        select(OrderMedia)
        .where(OrderMedia.order_id == order.id)
        .order_by(OrderMedia.position)
    )
    media_items = list(result.all())

    if not media_items:
        await callback.message.answer(text)
    else:
        media = []

        for i, item in enumerate(media_items):
            if i == 0:
                media.append(
                    InputMediaPhoto(
                        media=item.file_id,
                        caption=text[:1024]
                    )
                )
            else:
                media.append(InputMediaPhoto(media=item.file_id))

        if len(media) == 1:
            await callback.message.answer_photo(
                photo=media[0].media,
                caption=text[:1024]
            )
        else:
            await callback.message.answer_media_group(media=media)


    # Yakunlangan yoki bekor qilingan buyurtma tahrirlanmaydi
    if order.status not in {OrderStatus.COMPLETED, OrderStatus.CANCELLED}:
        edit_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✏️ Tahrirlash",
                        callback_data=f"edit_order:{order.id}"
                    )
                ]
            ]
        )
        await callback.message.answer(
            "Buyurtma ma'lumotlarini o'zgartirish uchun tugmani bosing:",
            reply_markup=edit_keyboard
        )
        await callback.answer()
@router.message(F.text.startswith("/cancel "))
async def cancel(message: Message, session: AsyncSession):
    if not await admin_required(message, session):
        return

    try:
        oid = int(message.text.split()[1])
    except:
        await message.answer("/cancel ORDER_ID")
        return
    await message.answer("✅ Bekor qilindi.")

@router.message(F.text == "📊 Hisobotlar")
async def reports(
    message: Message,
    session: AsyncSession
):
    if not await admin_required(message, session, True):
        return

    try:
        file_bytes = await create_excel_report(session)

        document = BufferedInputFile(
            file_bytes,
            filename=(
                f"Umumiy_hisobot_"
                f"{datetime.now():%Y-%m-%d_%H-%M}.xlsx"
            )
        )

        await message.answer_document(
            document=document,
            caption="📊 Umumiy Excel hisoboti"
        )

    except Exception:
        import logging
        logging.exception("Excel hisobot yaratishda xatolik")
        await message.answer(
            "❌ Hisobot yaratishda xatolik yuz berdi. "
            "Terminaldagi xatolikni tekshiring."
        )


@router.callback_query(F.data == "balances:excel")
async def balances_excel(
    callback: CallbackQuery,
    session: AsyncSession
):
    if not await admin_required(callback, session, True):
        await callback.answer()
        return

    try:
        file_bytes = await create_excel_report(session)

        document = BufferedInputFile(
            file_bytes,
            filename=(
                f"Umumiy_hisobot_"
                f"{datetime.now():%Y-%m-%d_%H-%M}.xlsx"
            )
        )

        await callback.message.answer_document(
            document=document,
            caption="📊 Umumiy Excel hisoboti"
        )
        await callback.answer("Hisobot tayyor!")

    except Exception:
        import logging
        logging.exception("Excel hisobot yaratishda xatolik")
        await callback.answer(
            "Excel yaratishda xatolik yuz berdi.",
            show_alert=True
        )


@router.callback_query(F.data.startswith("edit_order:"))
async def edit_order_start(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    order_id = int(callback.data.split(":")[1])
    order = await session.get(Order, order_id)

    if not order:
        await callback.answer("Buyurtma topilmadi!", show_alert=True)
        return

    if order.status in {OrderStatus.COMPLETED, OrderStatus.CANCELLED}:
        await callback.answer(
            "Yakunlangan yoki bekor qilingan buyurtmani tahrirlab bo'lmaydi.",
            show_alert=True
        )
        return

    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Tavsifi",
                    callback_data=f"edit_field:{order.id}:description"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚡ Ustuvorlik",
                    callback_data=f"edit_field:{order.id}:priority"
                )
            ]
        ]
    )

    await callback.message.answer(
        f"✏️ #{order.id} buyurtmani tahrirlash.\n"
        "O'zgartirmoqchi bo'lgan maydonni tanlang:",
        reply_markup=keyboard
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_field:"))
async def edit_field_start(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    parts = callback.data.split(":")
    order_id = int(parts[1])
    field = parts[2]

    order = await session.get(Order, order_id)

    if not order:
        await callback.answer("Buyurtma topilmadi!", show_alert=True)
        return

    if order.status in {OrderStatus.COMPLETED, OrderStatus.CANCELLED}:
        await callback.answer(
            "Bu buyurtmani tahrirlash mumkin emas.",
            show_alert=True
        )
        return

    if field not in {"description", "priority"}:
        await callback.answer("Noto'g'ri maydon!", show_alert=True)
        return

    await state.update_data(edit_order_id=order_id, edit_field=field)
    await state.set_state(EditOrder.value)

    if field == "description":
        await callback.message.answer(
            f"📝 Buyurtma #{order.id} uchun yangi tavsifni yuboring.\n\n"
            f"Hozirgi tavsif: {order.description or '-'}"
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🟢 Oddiy",
                        callback_data=f"edit_priority:{order.id}:NORMAL"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔴 Shoshilinch",
                        callback_data=f"edit_priority:{order.id}:URGENT"
                    )
                ]
            ]
        )
        await callback.message.answer(
            "⚡ Buyurtmaning yangi ustuvorligini tanlang:",
            reply_markup=keyboard
        )

    await callback.answer()    

@router.message(EditOrder.value, F.text)
async def edit_order_save_value(
    message: Message,
    session: AsyncSession,
    state: FSMContext
):
    data = await state.get_data()

    # Bu faqat tavsifni o'zgartirish uchun ishlaydi
    if data.get("edit_field") != "description":
        return

    order_id = data.get("edit_order_id")
    order = await session.get(Order, order_id)

    if not order:
        await message.answer("❌ Buyurtma topilmadi!")
        await state.clear()
        return

    if order.status in {OrderStatus.COMPLETED, OrderStatus.CANCELLED}:
        await message.answer(
            "❌ Yakunlangan yoki bekor qilingan buyurtmani tahrirlab bo'lmaydi."
        )
        await state.clear()
        return

    yangi_tavsif = message.text.strip()

    if not yangi_tavsif:
        await message.answer("❌ Tavsif bo'sh bo'lmasligi kerak.")
        return

    
    order.description = yangi_tavsif
    await session.commit()

    notify_result = await notify_assigned_worker(
        message.bot,
        session,
        order,
        f"Yangi tavsif: {yangi_tavsif}"
    )

    await state.clear()

    await message.answer(
        f"✅ Buyurtma №{order.id} tavsifi o'zgartirildi!\n\n"
        f"📝 Yangi tavsif:\n{order.description}\n\n"
        f"{notify_result}"
    )

@router.callback_query(F.data.startswith("edit_priority:"))
async def edit_priority_save(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext
):
    if not await admin_required(callback, session):
        await callback.answer()
        return

    parts = callback.data.split(":")
    order_id = int(parts[1])
    priority_value = parts[2]

    order = await session.get(Order, order_id)

    if not order:
        await callback.answer(
            "Buyurtma topilmadi!",
            show_alert=True
        )
        return

    if order.status in {
        OrderStatus.COMPLETED,
        OrderStatus.CANCELLED
    }:
        await callback.answer(
            "Bu buyurtmani tahrirlash mumkin emas.",
            show_alert=True
        )
        await state.clear()
        return

    if priority_value == "NORMAL":
        order.priority = Priority.NORMAL
        priority_text = "🟢 Oddiy"
    elif priority_value == "URGENT":
        order.priority = Priority.URGENT
        priority_text = "🔴 Shoshilinch"
    else:
        await callback.answer(
            "Noto'g'ri ustuvorlik!",
            show_alert=True
        )
        return

    
    await session.commit()

    notify_result = await notify_assigned_worker(
        callback.bot,
        session,
        order,
        f"Ustuvorlik: {priority_text}"
    )

    await state.clear()

    await callback.message.answer(
        f"✅ Buyurtma №{order.id} ustuvorligi o'zgartirildi!\n\n"
        f"⚡ Yangi ustuvorlik: {priority_text}\n\n"
        f"{notify_result}"
    )

    await callback.answer("Saqlandi!")
