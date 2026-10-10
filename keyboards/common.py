from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from db.models import Role


def main_menu(role):
    rows = []

    if role in {Role.SUPER_ADMIN, Role.ASSISTANT_ADMIN}:
        rows += [
            [
                KeyboardButton(text="➕ Buyurtma yaratish"),
                KeyboardButton(text="📋 Buyurtmalar")
            ],
            [
                KeyboardButton(text="🔄 Ish jarayonidagi buyurtmalar")
            ],
            [
                KeyboardButton(text="📢 Xabar yuborish")
            ]
        ]

    if role == Role.SUPER_ADMIN:
        rows += [
            [
                KeyboardButton(text="👥 Foydalanuvchilar"),
                KeyboardButton(text="⚙️ Foizlar")
            ],
            [
                KeyboardButton(text="💳 Balanslar"),
                KeyboardButton(text="📊 Hisobotlar")
            ]
        ]

    if role in {Role.WELDER, Role.PAINTER, Role.COURIER}:
        rows += [
            [
                KeyboardButton(text="📥 Mavjud buyurtmalar")
            ],
            [
                KeyboardButton(text="🧰 Mening ishlarim"),
                KeyboardButton(text="💰 Mening balansim")
            ]
        ]

    if role == Role.AKFA:
        rows += [
            [
                KeyboardButton(text="🪟 Akfa topshiriqlari")
            ]
        ]

    if not rows:
        rows = [
            [
                KeyboardButton(text="🔄 Botni qayta ishga tushirish")
            ]
        ]

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True
    )

def claim_kb(order_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Qabul qilish",
                    callback_data=f"claim:{order_id}"
                )
            ]
        ]
    )


def complete_kb(order_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Tayyor",
                    callback_data=f"done:{order_id}"
                )
            ]
        ]
    )


def role_kb(user_id):
    data = [
        ("👨‍🏭 Payvandchi", "welder"),
        ("🎨 Bo'yoqchi", "painter"),
        ("🚚 Kuryer", "courier"),
        ("🪟 Akfachi", "akfa"),
        ("🛠 Yordamchi Admin", "assistant_admin"),
        ("🚫 Rolni olib tashlash", "user")
    ]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"setrole:{user_id}:{role}"
                )
            ]
            for text, role in data
        ]
    )

def welder_complete_kb(order_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🪟 AKFA",
                    callback_data=f"akfa:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Ishni yakunlash",
                    callback_data=f"done:{order_id}"
                )
            ]
        ]
    )