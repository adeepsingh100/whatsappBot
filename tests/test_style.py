from pathlib import Path

from bot.parser import load_chats
from bot.style import build_messages, build_style, similar_pairs

FIX = Path(__file__).parent / "fixtures"


def style():
    return build_style(load_chats(FIX, "Aman"))


def test_profile():
    p = style()["profile"]
    assert p["messages"] == 8
    assert p["language_share"]["hinglish"] > p["language_share"]["english"]
    assert "😂" in p["top_emojis"] and p["all_lowercase_share"] == 1.0
    assert p["ends_with"]["?"] == 0 and len(p["samples"]) == 8


def test_pairs():
    pairs = style()["pairs"]
    assert {"contact": "Rahul", "them": "bhai kal match dekhne chalega?\n7 baje",
            "me": "haan chal pakka 😂\nkaun kaun aa raha"} in pairs
    assert not any(p["them"] == "oye" for p in pairs)          # reply came >6h later


def test_retrieval_and_prompt():
    s = style()
    top = similar_pairs(s["pairs"], "kal match chalega kya", "Priya", k=1)
    assert top[0]["contact"] == "Rahul"                        # overlap beats contact boost
    msgs = build_messages(s, "Rahul", [(False, "hi"), (False, "paise chahiye"), (True, "haan")], sensitive=True)
    assert msgs[0]["role"] == "system" and "Never say you are an AI" in msgs[0]["content"]
    assert "commit to nothing" in msgs[0]["content"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert msgs[1]["content"] == "hi\npaise chahiye"


def test_human_touches():
    import random
    from bot.style import enforce_style, pronoun, same_contact
    assert same_contact("Savi", "Savi❤️") and same_contact("Utkarsh", "Utkarsh BA Bebo")
    assert not same_contact("Rahul", "Savi❤️") and not same_contact(None, "Savi")
    assert pronoun(["aap kaha ho", "apko pta", "aap aao"]) == "aap" and pronoun(["ok"]) is None
    prof = {"top_emojis": ["😂"], "emoji_message_share": 0.18, "ends_with": {"!": 0.0, ".": 0.0}}
    never = random.Random(0); never.random = lambda: 0.99      # emoji roll fails
    always = random.Random(0); always.random = lambda: 0.0
    assert enforce_style(["Delhi! 😊", "Good morning! ☀️"], prof, never) == ["Delhi", "Good morning"]
    assert enforce_style(["haha 😂😊", "ok."], prof, always) == ["haha 😂", "ok"]
    assert enforce_style(["😊"], prof, never) == []


def test_contact_prefix_and_spellings():
    from collections import Counter
    from bot.style import enforce_style, same_contact, spellings
    assert same_contact("Savita Hooda", "Savi❤️") and not same_contact("Sa", "Savi")
    sp = spellings(Counter({"hu": 50, "hoon": 2, "mai": 40, "main": 1, "nahi": 30, "nhi": 5}))
    assert sp == {"hoon": "hu", "main": "mai"}
    prof = {"spellings": sp, "ends_with": {}, "top_emojis": []}
    assert enforce_style(["Main theek hoon, main road pe"], prof) == ["Mai theek hu, mai road pe"]
