from enum import Enum


class DecisionAction(str, Enum):
    NO_TRADE = "NO_TRADE"
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
