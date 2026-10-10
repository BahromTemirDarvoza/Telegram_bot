from datetime import datetime
from decimal import Decimal
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from .models import User, Role, Settings, Order, OrderStatus, Priority, Assignment, AssignmentStatus, BalanceTransaction, OrderMedia

async def ensure_user(session: AsyncSession, tg_id: int, full_name: str, username: str | None, super_admin_id: int):
    user = await session.scalar(select(User).where(User.telegram_id == tg_id))
    if user:
        user.full_name = full_name
        user.username = username
        if tg_id == super_admin_id:
            user.role = Role.SUPER_ADMIN
        await session.commit()
        return user
    user = User(
        telegram_id=tg_id,
        full_name=full_name,
        username=username,
        role=Role.SUPER_ADMIN if tg_id == super_admin_id else Role.USER
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user

async def get_user_by_tg(session, tg_id):
    return await session.scalar(select(User).where(User.telegram_id == tg_id))

async def get_or_create_settings(session):
    s = await session.get(Settings, 1)
    if not s:
        s = Settings(id=1)
        session.add(s)
        await session.commit()
        await session.refresh(s)
    return s

async def create_order(session, data, media_ids, creator):
    s = await get_or_create_settings(session)
    order = Order(
        **data,
        created_by=creator.id,
        welder_percent=s.welder_percent,
        painter_percent=s.painter_percent,
        courier_percent=s.courier_percent,
    )
    session.add(order)
    await session.flush()
    for i, fid in enumerate(media_ids[:10]):
        session.add(OrderMedia(order_id=order.id, file_id=fid, position=i))
    await session.commit()
    await session.refresh(order)
    return order

async def list_available_orders(session, role):
    needed = {
        Role.WELDER: OrderStatus.NEW,
        Role.PAINTER: OrderStatus.WAITING_PAINT,
        Role.COURIER: OrderStatus.WAITING_COURIER
    }[role]
    stmt = select(Order).where(Order.status == needed).order_by(
        (Order.priority == Priority.URGENT).desc(),
        Order.deadline.is_(None),
        Order.deadline.asc(),
        Order.created_at.asc()
    )
    return list((await session.scalars(stmt)).all())

async def active_count(session, user_id):
    stmt = select(func.count()).select_from(Assignment).where(
        Assignment.user_id == user_id,
        Assignment.status == AssignmentStatus.ACTIVE
    )
    return int(await session.scalar(stmt) or 0)

async def claim_order(session, order_id, user):
    if user.role not in {Role.WELDER, Role.PAINTER, Role.COURIER}:
        return False, "Bu rol buyurtma qabul qila olmaydi."
    if await active_count(session, user.id) >= 3:
        return False, "Sizda 3 ta aktiv buyurtma bor."

    expected, next_status = {
        Role.WELDER: (OrderStatus.NEW, OrderStatus.WELDING),
        Role.PAINTER: (OrderStatus.WAITING_PAINT, OrderStatus.PAINTING),
        Role.COURIER: (OrderStatus.WAITING_COURIER, OrderStatus.DELIVERY)
    }[user.role]

    result = await session.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == expected)
        .values(status=next_status)
        .returning(Order.id)
    )
    if result.scalar_one_or_none() is None:
        await session.rollback()
        return False, "Bu buyurtmani boshqa ishchi olib bo'lgan."

    session.add(Assignment(order_id=order_id, user_id=user.id, role=user.role))
    await session.commit()
    return True, "Buyurtma sizga biriktirildi."

def percent_for(order, role):
    return {
        Role.WELDER: Decimal(order.welder_percent),
        Role.PAINTER: Decimal(order.painter_percent),
        Role.COURIER: Decimal(order.courier_percent)
    }[role]


async def complete_assignment(session, order_id, user, photo_file_id=None):
    a = await session.scalar(
        select(Assignment).where(
            Assignment.order_id == order_id,
            Assignment.user_id == user.id,
            Assignment.role == user.role,
            Assignment.status == AssignmentStatus.ACTIVE
        )
    )

    if not a:
        return False, "Aktiv ish topilmadi yoki bu ish allaqachon yakunlangan."

    order = await session.get(Order, order_id)

    if not order:
        return False, "Buyurtma topilmadi."

    if user.role in {
        Role.WELDER,
        Role.PAINTER,
        Role.COURIER
    } and not photo_file_id:
        return False, "Ishni yakunlash uchun mahsulot rasmini yuborishingiz shart."

    # 1. Buyurtma tugatilishidan oldingi balans
    old_balance = Decimal(str(user.balance or 0))

    # 2. Buyurtma uchun qo'shiladigan pulni hisoblash
    pct = percent_for(order, user.role)
    earned = (
        Decimal(str(order.price)) * pct / Decimal("100")
    ).quantize(Decimal("0.01"))

    # 3. Yangi balansni hisoblash va yangilash
    new_balance = old_balance + earned

    a.status = AssignmentStatus.DONE
    a.completed_at = datetime.utcnow()
    a.completion_photo_file_id = photo_file_id
    a.earned_amount = earned

    user.balance = new_balance

    session.add(
        BalanceTransaction(
            user_id=user.id,
            order_id=order.id,
            assignment_id=a.id,
            amount=earned,
            kind="earn",
            note=f"{user.role.value} {pct}%"
        )
    )

    order.status = {
        Role.WELDER: OrderStatus.WAITING_PAINT,
        Role.PAINTER: OrderStatus.WAITING_COURIER,
        Role.COURIER: OrderStatus.COMPLETED
    }[user.role]

    await session.commit()

    # 4. Balans haqidagi xabar
    msg = (
        "✅ Buyurtma yakunlandi!\n\n"
        f"💳 Oldingi balans: {old_balance:,.2f} so'm\n"
        f"➕ Qo'shilgan pul: {earned:,.2f} so'm\n"
        f"💰 Yangi balans: {new_balance:,.2f} so'm"
    )

    return True, msg


async def revert_stage(session, order_id, role):
    a = await session.scalar(
        select(Assignment).where(
            Assignment.order_id == order_id,
            Assignment.role == role,
            Assignment.status == AssignmentStatus.DONE
        ).order_by(Assignment.completed_at.desc())
    )
    if not a:
        return False, "Yakunlangan bosqich topilmadi."
    user = await session.get(User, a.user_id)
    earned = Decimal(a.earned_amount)
    user.balance = max(Decimal("0"), Decimal(user.balance) - earned)
    session.add(BalanceTransaction(
        user_id=user.id, order_id=order_id, assignment_id=a.id,
        amount=-earned, kind="revert", note="Bosqich ortga qaytarildi"
    ))
    a.status = AssignmentStatus.ACTIVE
    a.completed_at = None

    order = await session.get(Order, order_id)
    order.status = {
        Role.WELDER: OrderStatus.WELDING,
        Role.PAINTER: OrderStatus.PAINTING,
        Role.COURIER: OrderStatus.DELIVERY
    }[role]
    await session.commit()
    return True, "Bosqich ortga qaytarildi va balans qayta hisoblandi."

async def reset_balance(session, user):
    amount = Decimal(user.balance)
    if amount <= 0:
        return Decimal("0")
    user.balance = Decimal("0")
    session.add(BalanceTransaction(
        user_id=user.id, amount=-amount, kind="payout",
        note="Admin pulni berdi"
    ))
    await session.commit()
    return amount
