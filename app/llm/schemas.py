JUDGE_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": ["NO_TRADE", "ENTER_LONG", "ENTER_SHORT", "HOLD", "REDUCE", "CLOSE"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "size_multiplier": {"type": "number", "minimum": 0, "maximum": 1},
        "ttl_minutes": {"type": "integer", "minimum": 1, "maximum": 240},
        "reasons": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "stop_logic": {"type": "string"},
        "take_profit_logic": {"type": "string"},
    },
    "required": [
        "action",
        "confidence",
        "size_multiplier",
        "ttl_minutes",
        "reasons",
        "stop_logic",
        "take_profit_logic",
    ],
}
