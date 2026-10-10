from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import load_config
from db.repository import ensure_user
from keyboards.common import main_menu

router = Router()
cfg = load_config()


def role_name(role) -> str:
    role_names = {
        "super_admin": "Super administrator",
        "admin": "Administrator",
        "welder": "Payvandchi",
        "worker": "Ishchi",
        "painter": "Bo‘yoqchi",
        "akfachi": "Akfachi",
        "delivery": "Yetkazuvchi",
        "courier": "Yetkazuvchi",
    }

    return role_names.get(
        str(role.value).lower(),
        str(role.value)
    )


@router.message(CommandStart())
async def start(message: Message, session: AsyncSession):
    u = await ensure_user(
        session,
        message.from_user.id,
        message.from_user.full_name,
        message.from_user.username,
        cfg.super_admin_id
    )

    await message.answer(
        f"Assalomu alaykum!\nRol: {role_name(u.role)}",
        reply_markup=main_menu(u.role)
    )


@router.message(F.text == "🔄 Botni qayta ishga tushirish")
async def refresh_menu(
    message: Message,
    session: AsyncSession
):
    u = await ensure_user(
        session,
        message.from_user.id,
        message.from_user.full_name,
        message.from_user.username,
        cfg.super_admin_id
    )

    await message.answer(
        f"Assalomu alaykum!\n"
        f"Rol: {role_name(u.role)}",
        reply_markup=main_menu(u.role)
    )