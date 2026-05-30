"""Validate parser paths align with tokenizer_config.json chat_template reference."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
TOKENIZER_PATH = ROOT / "tokenizer_config.json"


def test_chat_template_documents_tool_call_format():
    data = json.loads(TOKENIZER_PATH.read_text(encoding="utf-8"))
    template = data.get("chat_template", "")
    assert "<tool_call>" in template
    assert "</tool_call>" in template
    assert '"name"' in template
    assert '"arguments"' in template
    assert "<tools>" in template


def test_parser_regex_matches_template_markers():
    from core.parser import AgentOutputParser

    parser = AgentOutputParser({})
    sample = (
        '<tool_call>\n'
        '{"name": "write_file", "arguments": {"path": "x.py", "content": "pass"}}\n'
        "</tool_call>"
    )
    content, _reason, calls = parser.process_llm_output({"content": sample})
    assert calls
    assert calls[0]["function"]["name"] == "write_file"


def test_tokenizer_class_is_qwen2():
    data = json.loads(TOKENIZER_PATH.read_text(encoding="utf-8"))
    assert data.get("tokenizer_class") == "Qwen2Tokenizer"


print("All tool_template_alignment tests passed.")
