# memory.py

import json
import os
from ..config import settings

# Use resolved path from settings
MEMORY_FILE = settings.resolved_memory_file

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {}
    with open(MEMORY_FILE, "r") as f:
        return json.load(f)

def save_memory(memory):
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

def get_memory_value(key):
    memory = load_memory()
    return memory.get(key)

def set_memory_value(key, value):
    memory = load_memory()
    memory[key] = value
    save_memory(memory)

def ensure_memory(key, prompt):
    value = get_memory_value(key)
    if value is None:
        value = input(f"🔍 {prompt}: ").strip()
        if value:
            set_memory_value(key, value)
    return value


def clear_memory():
    """Remove the memory file from disk. Used for full reset."""
    if os.path.exists(MEMORY_FILE):
        os.remove(MEMORY_FILE)
