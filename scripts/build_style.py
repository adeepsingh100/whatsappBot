"""Build data/style.json from exported chats in data/chats/.

Usage: python scripts/build_style.py [chats_dir] [out.json] [--upload]
--upload also stores it in the bot DB (DATABASE_URL), which is where the deployed bot reads it from.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.parser import load_chats  # noqa: E402
from bot.style import build_style  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
src = Path(args[0] if args else "data/chats")
out = Path(args[1] if len(args) > 1 else "data/style.json")
chats = load_chats(src, os.getenv("MY_NAME"))
if not chats:
    sys.exit(f"No chats found in {src}/ (expected WhatsApp .txt exports)")
for c in chats:
    print(f"{c.contact:<25} me={c.me:<15} {len(c.msgs):>6} msgs{'  (group: profile only)' if c.is_group else ''}")
style = build_style(chats)
out.write_text(json.dumps(style, ensure_ascii=False, indent=1))
p = style["profile"]
print(f"\n{p['messages']} of my messages, {len(style['pairs'])} reply pairs -> {out}")
print(f"avg {p['avg_words']} words, languages {p['language_share']}, emojis {' '.join(p['top_emojis'][:5])}")
if "--upload" in sys.argv:
    if not os.getenv("DATABASE_URL"):
        sys.exit("--upload needs DATABASE_URL")
    from bot.db import DB  # noqa: E402
    DB().set("style", out.read_text())
    print("Uploaded to DB. Restart the Render service (or wait for the next wake-up) to load it.")
