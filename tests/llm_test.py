"""The AI line must work with models that reply with thinking blocks first.   python tests/llm_test.py"""
import os, sys, tempfile, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["OUTREACH_HOME"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key-xyz"
import bot  # noqa: E402

B = lambda t, **k: types.SimpleNamespace(type=t, **k)
calls = []

class BadRequest(Exception):
    pass

def fake_module(refuse_disable):
    class Messages:
        def create(self, **kw):
            calls.append(kw)
            if refuse_disable and "extra_body" in kw:
                raise BadRequest("thinking disabled not supported")
            return types.SimpleNamespace(content=[B("thinking", thinking=""), B("text", text="  Saw your new Boston office.  ")])
    class Client:
        def __init__(self, **kw):
            self.messages = Messages()
    return types.SimpleNamespace(Anthropic=Client, BadRequestError=BadRequest)

cfg = {"models": {"personalize": "claude-haiku-5-5"}}
bad = 0
for refuse in (False, True):
    calls.clear()
    sys.modules["anthropic"] = fake_module(refuse)
    out = bot.llm(cfg, "personalize", "hi")
    ok = out == "Saw your new Boston office."
    print(("PASS " if ok else "FAIL ") + ("model that refuses thinking-off: retried, answer kept" if refuse else "thinking switched off, answer text extracted past the thinking block"), repr(out))
    bad += not ok
    if not refuse:
        ok2 = calls[0].get("extra_body") == {"thinking": {"type": "disabled"}}
        print(("PASS " if ok2 else "FAIL ") + "asks for no thinking")
        bad += not ok2
sys.exit(1 if bad else 0)
