# test_memory.py

import os
os.environ["MEMORY_FILE"] = "test_memory.json"

from resume_agent.storage.memory import get_memory_value, set_memory_value, ensure_memory, load_memory, save_memory

def test_memory():
    test_file = os.environ["MEMORY_FILE"]

    if os.path.exists(test_file):
        os.remove(test_file)

    set_memory_value("foo", "bar")
    assert get_memory_value("foo") == "bar"
    print("✅ set/get works")

    save_memory({"a": 1, "b": 2})
    assert load_memory() == {"a": 1, "b": 2}
    print("✅ save/load works")

    import builtins
    original_input = builtins.input
    builtins.input = lambda _: "My Python experience"
    assert ensure_memory("python_experience", "Where did you use Python?") == "My Python experience"
    builtins.input = original_input
    print("✅ ensure_memory prompts and stores")

    os.remove(test_file)

if __name__ == "__main__":
    test_memory()
