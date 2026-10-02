from datetime import datetime
from pathlib import Path

from bot.parser import contact_from_filename, load_chats, parse_chat

FIX = Path(__file__).parent / "fixtures"


def test_android_format():
    msgs = parse_chat((FIX / "WhatsApp Chat with Rahul.txt").read_text())
    texts = [m.text for m in msgs]
    assert msgs[0].sender == "Rahul" and msgs[0].text == "bhai kal match dekhne chalega?"
    assert msgs[0].ts == datetime(2026, 10, 1, 22, 0)
    assert "bas hum dono\nand maybe Vikas" in texts           # multi-line joined
    assert "theek hai 👍" in texts                              # edited marker stripped
    assert not any("Media omitted" in t or "deleted" in t or "encrypted" in t for t in texts)


def test_ios_format():
    msgs = parse_chat((FIX / "WhatsApp Chat - Priya.txt").read_text())
    assert [m.sender for m in msgs] == ["Priya", "Aman", "Aman", "Priya", "Aman"]
    assert msgs[0].ts == datetime(2026, 10, 2, 22, 15, 30)
    assert msgs[2].text == "they said will call next week\nfingers crossed 🤞"


def test_24h_and_month_first_dates():
    msgs = parse_chat("12/25/2025, 22:15 - A: hi\n25.12.25, 09:05 - B: yo")
    assert msgs[0].ts == datetime(2025, 12, 25, 22, 15)
    assert msgs[1].ts == datetime(2025, 12, 25, 9, 5)


def test_who_is_me(monkeypatch):
    assert contact_from_filename(Path("WhatsApp Chat with Rahul (2).txt")) == "Rahul"
    chats = {c.contact: c for c in load_chats(FIX)}            # no MY_NAME: inferred from filename
    assert chats["Rahul"].me == "Aman" and chats["Priya"].me == "Aman"
    assert all(c.me == "Aman" for c in load_chats(FIX, "Aman"))
