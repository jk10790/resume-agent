import builtins
from unittest.mock import patch

from resume_agent.storage import memory as memory_mod


def test_memory(tmp_path):
    test_file = tmp_path / "test_memory.json"

    with patch.object(memory_mod, "MEMORY_FILE", str(test_file)):
        from resume_agent.storage.memory import (
            ensure_memory,
            get_memory_value,
            load_memory,
            save_memory,
            set_memory_value,
        )

        set_memory_value("foo", "bar")
        assert get_memory_value("foo") == "bar"

        save_memory({"a": 1, "b": 2})
        assert load_memory() == {"a": 1, "b": 2}

        original_input = builtins.input
        builtins.input = lambda _prompt: "My Python experience"
        assert ensure_memory("python_experience", "Where did you use Python?") == "My Python experience"
        builtins.input = original_input

    assert test_file.exists()
