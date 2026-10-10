from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from sqlalchemy import BigInteger, String, Boolean, DateTime, Date, ForeignKey, Numeric, UniqueConstraint, Integer, Text, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    ASSISTANT_ADMIN = "assistant_admin"
    WELDER = "welder"
    PAINTER = "painter"
    COURIER = "courier"
    AKFA = "akfa"
    USER = "user"

class OrderStatus(str, Enum):
    NEW = "new"
    WELDING = "welding"
    WAITING_PAINT = "waiting_paint"
    PAINTING = "painting"
    WAITING_COURIER = "waiting_courier"
    DELIVERY = "delivery"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class Priority(str, Enum):
    NORMAL = "normal"
    URGENT = "urgent"

class AssignmentStatus(str, Enum):
    ACTIVE = "active"
    DONE = "done"
    REVERTED = "reverted"

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[Role] = mapped_column(SAEnum(Role), default=Role.USER, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18,2), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Settings(Base):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    welder_percent: Mapped[Decimal] = mapped_column(Numeric(5,2), default=Decimal("12"))
    painter_percent: Mapped[Decimal] = mapped_column(Numeric(5,2), default=Decimal("5"))
    courier_percent: Mapped[Decimal] = mapped_column(Numeric(5,2), default=Decimal("5"))

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    customer_name: Mapped[str] = mapped_column(String(255))
    customer_phone: Mapped[str] = mapped_column(String(100))
    customer_address: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(18,2))
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    priority: Mapped[Priority] = mapped_column(SAEnum(Priority), default=Priority.NORMAL, index=True)
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.NEW, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    welder_percent: Mapped[Decimal] = mapped_column(Numeric(5,2))
    painter_percent: Mapped[Decimal] = mapped_column(Numeric(5,2))
    courier_percent: Mapped[Decimal] = mapped_column(Numeric(5,2))
    has_akfa_task: Mapped[bool] = mapped_column(Boolean, default=False)

class OrderMedia(Base):
    __tablename__ = "order_media"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str] = mapped_column(String(512))
    position: Mapped[int] = mapped_column(Integer, default=0)

class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[Role] = mapped_column(SAEnum(Role), index=True)
    status: Mapped[AssignmentStatus] = mapped_column(SAEnum(AssignmentStatus), default=AssignmentStatus.ACTIVE, index=True)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completion_photo_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    earned_amount: Mapped[Decimal] = mapped_column(Numeric(18,2), default=Decimal("0"))

class AkfaTask(Base):
    __tablename__ = "akfa_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    created_by_welder_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="waiting", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

class BalanceTransaction(Base):
    __tablename__ = "balance_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2))
    kind: Mapped[str] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class PayrollPeriod(Base):
    __tablename__ = "payroll_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        index=True
    )
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    earned_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0")
    )
    advance_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0")
    )
    salary_paid: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0")
    )
    closed: Mapped[bool] = mapped_column(
        Boolean,
        default=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )
    