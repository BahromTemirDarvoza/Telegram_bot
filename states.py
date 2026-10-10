from aiogram.fsm.state import State, StatesGroup


class CreateOrder(StatesGroup):
    title = State()
    description = State()
    customer_name = State()
    customer_phone = State()
    customer_address = State()
    price = State()
    deadline = State()
    priority = State()
    media = State()
    review = State()
    edit_value = State()
    review = State()
    edit_value = State()    

class AssignRole(StatesGroup):
    telegram_id = State()

class SetPercent(StatesGroup):
    values = State()

class FinishPainter(StatesGroup):
    photo = State()

class Broadcast(StatesGroup):
    target = State()
    message = State()

class AkfaCreate(StatesGroup):
    text = State()


class WorkerPayment(StatesGroup):
    amount = State()


    
class EditOrder(StatesGroup):
    field = State()
    value = State()

class AkfaDimensions(StatesGroup):
    order_id = State()
    dimensions = State()    