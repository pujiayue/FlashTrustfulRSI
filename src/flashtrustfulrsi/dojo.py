"""Serialize native AgentDojo tool results."""
def encode(value):
    if hasattr(value,'model_dump'):return value.model_dump(mode='json')
    raise TypeError(type(value).__name__)
