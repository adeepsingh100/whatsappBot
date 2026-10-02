"""Chat with your twin in the terminal before going live.

Usage: python scripts/try_reply.py [contact name] [--prompt]
Type their messages; an empty line asks for the reply. /reset clears the chat, Ctrl-D quits.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import llm  # noqa: E402
from bot.rules import clean_reply, is_sensitive  # noqa: E402
from bot.style import build_messages  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
contact = args[0] if args else None
show_prompt = "--prompt" in sys.argv
safe = os.getenv("SAFE_MODE", "true").lower() not in ("0", "false", "no", "off")
style_path = Path(os.getenv("STYLE_PATH", "data/style.json"))
style = json.loads(style_path.read_text()) if style_path.exists() else {}
if not style:
    print(f"(no {style_path}: run scripts/build_style.py first; replying without your style)")
if not llm.providers():
    sys.exit("Set NVIDIA_API_KEY, OPENROUTER_API_KEY or OLLAMA_URL first.")

history, pending = [], []
print(f"Talking as {contact or 'a contact'}. Empty line = get reply.")
while True:
    try:
        line = input("them> ").strip()
    except EOFError:
        break
    if line == "/reset":
        history, pending = [], []
        continue
    if line:
        history.append((False, line))
        pending.append(line)
        continue
    if not pending:
        continue
    sensitive = safe and is_sensitive(pending)
    msgs = build_messages(style, contact, history[-10:], safe, sensitive)
    if show_prompt:
        print(msgs[0]["content"], "\n" + "-" * 40)
    out = clean_reply(asyncio.run(llm.complete(msgs)))
    for r in out or ["(no reply: empty or looked like an AI answer)"]:
        print(f"  me> {r}")
    if sensitive:
        print("  (safe mode: sensitive topic)")
    history += [(True, r) for r in out]
    pending = []
