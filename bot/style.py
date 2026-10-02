"""Style profile + example pairs from parsed chats, retrieval and prompt building."""
import random
import re
from collections import Counter
from datetime import timedelta

from bot.parser import Chat

TOKEN = re.compile(r"[\wऀ-ॿ]+")
EMOJI = re.compile("[\U0001F000-\U0001F3FA\U0001F400-\U0001FAFF☀-➿⭐⭕‼⁉]")
DEVANAGARI = re.compile("[ऀ-ॿ]")
LATIN = re.compile("[A-Za-z]")
# ponytail: word-list Hinglish detector, misses rare words; fine for a share estimate
HINGLISH = set("""hai hain nahi nhi nai kya kyu kyun haan han haa acha accha achha theek thik kar karo karna
raha rahi rahe hoon hun mein mai tum tu aap yaar yr bhai kal aaj abhi kuch kuchh kaise kaisa kese bata batao
batata chal chalo sahi matlab pata bas toh bhi gaya gayi gya wala wali ji mujhe tujhe kab kahan kidhar dekhta
dekh baad pehle phir fir hua hogya ho gaya kaha bol bolo mast arre arey haina krna kr kro ab""".split())
PAIR_MAX_GAP = timedelta(hours=6)

SYSTEM_PROMPT = ("You are me, replying on WhatsApp. Match my style exactly (length, language mix, emojis, "
                 "casing). Never say you are an AI. Keep it short like I do.")
SAFE_RULE = ("If the message is about money, payments, loans, promises, commitments, making plans or meeting, "
             "or bad/serious news, do NOT agree, confirm, promise or decide anything. Reply vaguely in my style, "
             "like \"dekhta hoon, baad mein batata hoon\".")


def tokens(text: str) -> set[str]:
    return set(TOKEN.findall(text.lower()))


def _share(items, pred) -> float:
    items = list(items)
    return round(sum(map(pred, items)) / len(items), 3) if items else 0.0


def profile(texts: list[str], n_samples: int = 40) -> dict:
    texts = [t for t in texts if t.strip()]
    if not texts:
        return {"messages": 0}
    words = [TOKEN.findall(t.lower()) for t in texts]
    emojis = Counter(e for t in texts for e in EMOJI.findall(t))
    latin = [t for t in texts if LATIN.search(t)]

    def lang(t, w):
        if DEVANAGARI.search(t):
            return "devanagari"
        if HINGLISH & set(w):
            return "hinglish"
        return "english" if LATIN.search(t) else "other"

    langs = Counter(lang(t, w) for t, w in zip(texts, words))
    stripped = [EMOJI.sub("", t).rstrip() for t in texts]
    uniq = list(dict.fromkeys(t for t in texts if len(t) <= 200))
    return {
        "messages": len(texts),
        "avg_chars": round(sum(map(len, texts)) / len(texts), 1),
        "avg_words": round(sum(map(len, words)) / len(texts), 1),
        "very_short_share": _share(words, lambda w: len(w) <= 3),
        "emoji_message_share": _share(texts, lambda t: bool(EMOJI.search(t))),
        "top_emojis": [e for e, _ in emojis.most_common(10)],
        "all_lowercase_share": _share(latin, lambda t: t == t.lower()),
        "starts_lowercase_share": _share(latin, lambda t: LATIN.search(t).group().islower()),
        "ends_with": {
            ".": _share(stripped, lambda t: t.endswith(".") and not t.endswith("..")),
            "!": _share(stripped, lambda t: t.endswith("!")),
            "?": _share(stripped, lambda t: t.endswith("?")),
            "...": _share(stripped, lambda t: t.endswith("..")),
            "none": _share(stripped, lambda t: t[-1:].isalnum()),
        },
        "language_share": {k: round(v / len(texts), 3) for k, v in langs.most_common()},
        "top_openers": [w for w, _ in Counter(w[0] for w in words if w).most_common(15)],
        "top_words": [w for w, _ in Counter(x for w in words for x in w).most_common(40)],
        "samples": random.Random(42).sample(uniq, min(n_samples, len(uniq))),
    }


def make_pairs(chat: Chat, max_them: int = 3, max_me: int = 5) -> list[dict]:
    """(their consecutive messages -> my consecutive messages), skipping replies after long gaps."""
    blocks = []  # [sender, [msgs]]
    for m in chat.msgs:
        if blocks and blocks[-1][0] == m.sender:
            blocks[-1][1].append(m)
        else:
            blocks.append([m.sender, [m]])
    pairs = []
    for (s1, them), (s2, mine) in zip(blocks, blocks[1:]):
        if s1 == chat.me or s2 != chat.me:
            continue
        if them[-1].ts and mine[0].ts and mine[0].ts - them[-1].ts > PAIR_MAX_GAP:
            continue
        pairs.append({"contact": chat.contact,
                      "them": "\n".join(m.text for m in them[-max_them:]),
                      "me": "\n".join(m.text for m in mine[:max_me])})
    return pairs


def build_style(chats: list[Chat]) -> dict:
    mine = [m.text for c in chats for m in c.msgs if m.sender == c.me]
    pairs = [p for c in chats if not c.is_group for p in make_pairs(c)]
    return {"profile": profile(mine), "pairs": pairs}


def similar_pairs(pairs: list[dict], query: str, contact: str | None, k: int = 6,
                  contact_boost: float = 0.15) -> list[dict]:
    # ponytail: re-tokenizes every pair per call, O(pairs); cache tokens if style.json grows past ~50k pairs
    q = tokens(query)

    def score(p):
        t = tokens(p["them"])
        return len(q & t) / (len(q | t) or 1) + (contact_boost if contact and p["contact"] == contact else 0)

    return sorted(pairs, key=score, reverse=True)[:k]


def describe(p: dict) -> str:
    if not p.get("messages"):
        return ""
    langs = ", ".join(f"{k} {round(v * 100)}%" for k, v in p["language_share"].items())
    ends = ", ".join(f"'{k}' {round(v * 100)}%" for k, v in p["ends_with"].items())
    return "\n".join([
        f"- Average message: {p['avg_words']} words / {p['avg_chars']} chars; "
        f"{round(p['very_short_share'] * 100)}% of my messages are 3 words or less.",
        f"- Language mix: {langs}.",
        f"- Emojis in {round(p['emoji_message_share'] * 100)}% of messages; favourites: {' '.join(p['top_emojis'][:6]) or 'none'}.",
        f"- Fully lowercase {round(p['all_lowercase_share'] * 100)}% of the time.",
        f"- Message endings: {ends}.",
        f"- Words I often start with: {', '.join(p['top_openers'][:10])}.",
        f"- Words I use a lot: {', '.join(p['top_words'][:25])}.",
    ])


def build_messages(style: dict, contact: str | None, history: list[tuple[bool, str]],
                   safe_mode: bool = True, sensitive: bool = False, n_samples: int = 15) -> list[dict]:
    """OpenAI-style chat messages. history = [(from_me, text), ...] oldest first, last ~10."""
    prof = style.get("profile", {})
    incoming = "\n".join(t for mine, t in history[-3:] if not mine)
    shots = similar_pairs(style.get("pairs", []), incoming, contact)
    parts = [SYSTEM_PROMPT]
    if d := describe(prof):
        parts.append("My style:\n" + d)
    if prof.get("samples"):
        parts.append("Some of my real messages:\n" + "\n".join(f"- {s}" for s in prof["samples"][:n_samples]))
    if shots:
        parts.append("How I replied in similar situations:\n" + "\n\n".join(
            f"[{p['contact']}]: {p['them']}\n[me]: {p['me']}" for p in shots))
    if safe_mode:
        parts.append(SAFE_RULE)
        if sensitive:
            parts.append("This conversation touches one of those topics: stay vague, commit to nothing.")
    parts.append(f"You are chatting with {contact or 'a contact'}. Reply with only my next message text. "
                 "If I'd send several short messages, put each on its own line.")
    msgs = [{"role": "system", "content": "\n\n".join(parts)}]
    for mine, text in history:
        role = "assistant" if mine else "user"
        if msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + text
        else:
            msgs.append({"role": role, "content": text})
    return msgs
