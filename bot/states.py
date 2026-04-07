from aiogram.fsm.state import State, StatesGroup


class AddProductStates(StatesGroup):
    waiting_for_url = State()


class BroadcastStates(StatesGroup):
    waiting_for_message = State()
