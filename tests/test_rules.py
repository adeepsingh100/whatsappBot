import random
from datetime import datetime, timezone

from bot.rules import (clean_reply, command, human_delay, is_direct_chat, is_self_chat, is_sensitive,
                       parse_message, too_old)


def evt(text="hi", chat="919800000001@s.whatsapp.net", sender=None, from_me=False, group=False,
        ts="2026-10-02T10:15:30Z", mid="A1", **extra):
    msg = {"conversation": text} if text is not None else {"imageMessage": {"url": "x"}}
    return {"event": "Message", "instanceToken": "tok",
            "data": {"Info": {"ID": mid, "Chat": chat, "Sender": sender or chat, "IsFromMe": from_me,
                              "IsGroup": group, "PushName": "Rahul", "Timestamp": ts, **extra},
                     "Message": msg}}


def test_parse_message():
    m = parse_message(evt())
    assert (m.id, m.text, m.push_name, m.from_me) == ("A1", "hi", "Rahul", False)
    T = datetime(2026, 10, 2, 10, 15, 30, tzinfo=timezone.utc).timestamp()
    assert m.ts == T
    ext = evt(); ext["data"]["Message"] = {"extendedTextMessage": {"text": " link https://x.y "}}
    assert parse_message(ext).text == "link https://x.y"
    assert parse_message(evt(text=None)).text is None                       # media-only
    assert parse_message(evt(ts="2026-10-02T15:45:30.123456789+05:30")).ts == T + 0.123456
    assert parse_message({"event": "QRCode", "data": {}}) is None


def test_filters():
    assert is_direct_chat(parse_message(evt()))
    assert is_direct_chat(parse_message(evt(chat="12345@lid")))
    assert not is_direct_chat(parse_message(evt(chat="123-456@g.us", group=True)))
    assert not is_direct_chat(parse_message(evt(chat="status@broadcast")))
    m = parse_message(evt())
    assert not too_old(m, m.ts + 29 * 60) and too_old(m, m.ts + 31 * 60)


def test_self_chat():
    me = "919811111111"
    assert is_self_chat(parse_message(evt(chat=f"{me}@s.whatsapp.net", sender=f"{me}:12@s.whatsapp.net",
                                          from_me=True)))
    lid = parse_message(evt(chat="777@lid", sender=f"{me}:3@s.whatsapp.net", from_me=True, SenderAlt="777:3@lid"))
    assert is_self_chat(lid)
    assert is_self_chat(parse_message(evt(chat=f"{me}@s.whatsapp.net", sender="888@lid", from_me=True)), "+91 98111 11111")
    assert not is_self_chat(parse_message(evt(sender=f"{me}@s.whatsapp.net", from_me=True)))   # me, in Rahul's chat
    assert not is_self_chat(parse_message(evt(chat=f"{me}@s.whatsapp.net")))                  # not from me


def test_commands_and_safe_mode():
    assert command(" /ON ") == "/on" and command("/status") == "/status" and command("/onx") is None
    assert is_sensitive(["bhai 2000 udhaar de"]) and is_sensitive(["can you pay me back"])
    assert is_sensitive(["kal milte hai?"]) and is_sensitive(["uske papa hospital mein hai"])
    assert not is_sensitive(["haha mast", "kya kar raha"])


def test_reply_cleanup_and_timing():
    assert clean_reply('me: "haan bhai\n\nkal pakka"') == ["haan bhai", "kal pakka"]
    assert clean_reply("As an AI, I can't do that") == []
    assert clean_reply("   ") == []
    rng = random.Random(1)
    assert all(15 <= human_delay("x" * n, rng) <= 90 for n in (0, 20, 400))
    assert human_delay("ok", random.Random(1)) < human_delay("x" * 150, random.Random(1))
