"""
Basic sanity tests — ensures Python files are importable and structured correctly.
These tests do NOT require a live Telegram connection or MongoDB.
"""


def test_config_has_required_fields():
    """config.py must define API_ID, API_HASH, BOT_TOKEN."""
    import ast
    import os

    config_path = os.path.join(os.path.dirname(__file__), "config.py")
    with open(config_path, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)
    names = {
        node.targets[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
    }

    for field in ("API_ID", "API_HASH", "BOT_TOKEN"):
        assert field in names, f"config.py is missing required field: {field}"


def test_handler_files_are_valid_python():
    """All handler .py files must be syntactically valid Python."""
    import ast
    import glob
    import os

    handlers_dir = os.path.join(os.path.dirname(__file__), "handlers")
    py_files = glob.glob(os.path.join(handlers_dir, "*.py"))
    assert py_files, "No handler files found"

    for filepath in py_files:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
        try:
            ast.parse(source)
        except SyntaxError as e:
            raise AssertionError(f"Syntax error in {filepath}: {e}") from e


def test_new_handlers_present():
    """New handler files must exist."""
    import os

    handlers_dir = os.path.join(os.path.dirname(__file__), "handlers")
    for name in ("control_group.py", "protection.py", "keyword_reply.py"):
        path = os.path.join(handlers_dir, name)
        assert os.path.isfile(path), f"Missing handler file: {name}"


def test_ai_reply_disabled_in_init():
    """ai_reply must NOT be imported in handlers/__init__.py."""
    import os

    init_path = os.path.join(os.path.dirname(__file__), "handlers", "__init__.py")
    with open(init_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = [
        line for line in content.splitlines()
        if "ai_reply" in line and not line.strip().startswith("#")
    ]
    assert not lines, (
        "ai_reply should be disabled (commented out) in handlers/__init__.py, "
        f"but found active lines: {lines}"
    )
