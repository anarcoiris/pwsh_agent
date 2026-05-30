"""Quick verification for parser bare-JSON extraction fix."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools
from core.parser import AgentOutputParser

REG = {
    "write_file": tools.write_file,
    "read_file": tools.read_file,
    "host_exec": tools.host_exec,
    "sequentialthinking": lambda x: x,
    "list_network_interfaces": tools.list_network_interfaces,
}
parser = AgentOutputParser(REG)

CASES = [
    (
        '{"name": "write_file", "arguments": {"path": "workspace/test_tools.txt", "content": "hello"}}',
        "write_file",
    ),
    (
        '<tool_call>{"name": "read_file", "arguments": {"path": "x.txt"}}</tool_call>',
        "read_file",
    ),
    (
        '```json\n{"name": "host_exec", "arguments": {"command": "echo hi"}}\n```',
        "host_exec",
    ),
    (
        '{"name": "list_network_interfaces", "arguments": {}}',
        "list_network_interfaces",
    ),
    (
        '```python\nimport os\nprint("hi")\n```',
        "write_file",
    ),
]

USER_CTX = "save watcher.py in the watcher folder"

for content, expected in CASES:
    calls = parser._discover_and_extract_tool_calls(content)
    names = [c["function"]["name"] for c in calls]
    assert names and names[0] == expected, f"FAIL {content[:50]!r} -> {names}"
    salvaged = parser.salvage_tool_call(content)

# Code block path inference
py_block = '```python\nimport os\n\ndef main():\n    pass\n```'
calls = parser._discover_and_extract_tool_calls(py_block, user_context=USER_CTX)
assert calls[0]["function"]["name"] == "write_file"
assert calls[0]["function"]["arguments"]["path"] == "watcher/watcher.py"

print("All parser extraction tests passed.")
