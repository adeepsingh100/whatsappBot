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


# standard spelling -> my usual variants; enforce_style swaps in mine when I clearly prefer it
SPELLING_VARIANTS = {"hoon": ["hu", "hun"], "main": ["mai"], "nahi": ["nhi", "ni"], "kiya": ["kia"],
                     "batao": ["btao"], "bas": ["bs"], "raha": ["rha", "ra"], "rahi": ["rhi", "ri"],
                     "rahe": ["rhe", "re"], "karo": ["kro"], "karna": ["krna"], "theek": ["thik", "thk"],
                     "accha": ["acha", "achha"], "kyun": ["kyu"], "mujhe": ["merko", "mereko", "mjhe"],
                     "phir": ["fir"], "toh": ["to"], "kuch": ["kch"], "abhi": ["abi"]}


def spellings(counts: Counter) -> dict:
    out = {}
    for std, variants in SPELLING_VARIANTS.items():
        mine = max(variants, key=lambda v: counts[v])
        if mine != std and counts[mine] >= 3 * max(counts[std], 1):
            out[std] = mine
    return out


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
        "spellings": spellings(Counter(x for w in words for x in w)),
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
    prof = profile(mine)
    if pairs:  # how many messages I send per reply: share of 1, 2, 3, 4+
        n = Counter(min(p["me"].count("\n") + 1, 4) for p in pairs)
        prof["reply_lines"] = [round(n[i] / len(pairs), 3) for i in range(1, 5)]
    return {"profile": prof, "pairs": pairs}


def same_contact(a: str | None, b: str | None) -> bool:
    """Push name vs export name, by first word: 'Savi' ~ 'Savi❤️', 'Utkarsh' ~ 'Utkarsh BA Bebo'."""
    wa, wb = TOKEN.findall((a or "").lower()), TOKEN.findall((b or "").lower())
    if not (wa and wb):
        return False
    x, y = sorted((wa[0], wb[0]), key=len)
    return len(x) >= 3 and y.startswith(x)  # 'savi' ~ 'savita'


def similar_pairs(pairs: list[dict], query: str, contact: str | None, k: int = 8,
                  contact_boost: float = 0.15) -> list[dict]:
    # ponytail: re-tokenizes every pair per call, O(pairs); cache tokens if style.json grows past ~50k pairs
    q = tokens(query)
    contacts = {c: same_contact(c, contact) for c in {p["contact"] for p in pairs}} if contact else {}

    def score(p):
        t = tokens(p["them"])
        return len(q & t) / (len(q | t) or 1) + (contact_boost if contacts.get(p["contact"]) else 0)

    return sorted(pairs, key=score, reverse=True)[:k]


PRONOUNS = {"aap": {"aap", "ap", "apko", "apke", "apka", "apki", "apne", "aapko", "aapke", "aapka"},
            "tu": {"tu", "tujhe", "tera", "teri", "tere", "tujhko"},
            "tum": {"tum", "tumhe", "tumhara", "tumhari", "tumko"}}


def pronoun(texts: list[str]) -> str | None:
    """Which 'you' I use with someone, from my messages to them."""
    words = Counter(w for t in texts for w in TOKEN.findall(t.lower()))
    counts = {k: sum(words[w] for w in v) for k, v in PRONOUNS.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] >= 3 else None


def describe(p: dict) -> str:
    if not p.get("messages"):
        return ""
    langs = ", ".join(f"{k} {round(v * 100)}%" for k, v in p["language_share"].items())
    ends = ", ".join(f"'{k}' {round(v * 100)}%" for k, v in p["ends_with"].items())
    return "\n".join([
        f"- Average message: {p['avg_words']} words / {p['avg_chars']} chars; "
        f"{round(p['very_short_share'] * 100)}% of my messages are 3 words or less.",
        f"- Language mix: {langs}.",
        f"- Emojis in only {round(p['emoji_message_share'] * 100)}% of messages, and only these: "
        f"{' '.join(p['top_emojis'][:6]) or 'none'}.",
        f"- Fully lowercase {round(p['all_lowercase_share'] * 100)}% of the time.",
        f"- Message endings: {ends}.",
        f"- Words I often start with: {', '.join(p['top_openers'][:10])}.",
        f"- Words I use a lot: {', '.join(p['top_words'][:25])}.",
    ])


HUMAN_RULES = """Sound like me texting from my phone, not like an assistant:
- Copy my spellings and short forms from the examples exactly (don't "correct" them into proper Hindi or English).
- No "!" and no emoji unless my examples would have one. Never 😊 ☀️ 👋 style filler.
- No assistant phrases ("Bilkul", "Sure", "Of course", "How can I help", "Let me know"), no over-friendly questions.
- Often just answer in 1-5 words. Don't always ask a question back.
- Answer only what they actually said. Don't add extra lines that react to things nobody said ("You too",
  "Tension mt lo") or that contradict your first line.
- Usually ONE short line (under 10 words). Never paragraphs, never markdown, never "..." trailing off.
- Never describe photos, actions or feelings in [brackets] or *asterisks*. You can't send photos or make calls.
- Never promise to call, meet, send something or do something later.
- If asked to do work (code, essays, homework, quiz/trivia, recommendations lists), don't do it; react like a
  busy friend would (e.g. "khud kr le 😂", "baad mai").
- Never discuss whether you are a bot/AI. Never admit it, never argue about it.
- Never invent facts about my life, people, places, plans or feelings. If you don't know, reply vaguely or casually ask back."""


def build_messages(style: dict, contact: str | None, history: list[tuple[bool, str]],
                   safe_mode: bool = True, sensitive: bool = False, n_samples: int = 15,
                   group: bool = False, n_new: int = 1) -> list[dict]:
    """OpenAI-style chat messages. history = [(from_me, text), ...] oldest first, last ~10."""
    prof = style.get("profile", {})
    pairs = style.get("pairs", [])
    incoming = "\n".join(t for mine, t in history[-3:] if not mine)
    shots = similar_pairs(pairs, incoming, contact)
    theirs = [p["me"] for p in pairs if contact and same_contact(p["contact"], contact)]
    parts = [SYSTEM_PROMPT]
    if style.get("about"):
        parts.append("Facts about me (the ONLY facts you may use):\n" + style["about"].strip())
    if d := describe(prof):
        parts.append("My style:\n" + d)
    if you := pronoun(theirs or [p["me"] for p in shots]):
        parts.append(f"With this person I say '{you}' for 'you'.")
    samples = random.Random(len(history)).sample(theirs, min(n_samples, len(theirs))) if theirs else \
        prof.get("samples", [])[:n_samples]
    if samples:
        parts.append("Some of my real messages" + (" to this person" if theirs else "") + ":\n" +
                     "\n".join(f"- {s}" for s in samples))
    if shots:
        parts.append("How I replied in similar situations:\n" + "\n\n".join(
            f"[{p['contact']}]: {p['them']}\n[me]: {p['me']}" for p in shots))
    parts.append(HUMAN_RULES)
    if safe_mode:
        parts.append(SAFE_RULE)
        if sensitive:
            parts.append("This conversation touches one of those topics: stay vague, commit to nothing.")
    parts.append('A line starting with [replying to ... message: "..."] is a swipe-reply to that quoted message; '
                 "answer it in that context.")
    if n_new > 1:
        parts.append(f"They sent {n_new} messages in a row. Answer each one that needs an answer, "
                     "one short line each, in order. Skip ones that need no answer.")
    if group:
        parts.append("This is a GROUP chat: their lines start with 'Name: '. Reply like I'd chip in to the group, "
                     "in one short line; reply to the latest message(s).")
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


def enforce_style(lines: list[str], prof: dict, rng: random.Random = random, min_lines: int = 1) -> list[str]:
    """Strip what I never do: '!' / '.' endings I don't use, emojis outside my set, emojis above my rate."""
    swaps = prof.get("spellings", {})
    allowed = set(prof.get("top_emojis", []))
    ends = prof.get("ends_with", {})
    keep_emoji = rng.random() < prof.get("emoji_message_share", 0.2) * 1.5
    weights = prof.get("reply_lines")
    if weights:  # send as many messages as I usually do; the first lines are the actual answer
        lines = lines[:max(rng.choices(range(1, 5), weights=weights)[0], min(min_lines, 3))]
    out = []
    for ln in lines:
        ln = EMOJI.sub(lambda m: m.group() if keep_emoji and m.group() in allowed else "", ln)
        ln = ln.replace("\ufe0f", "") if not keep_emoji else ln
        if ends.get("!", 1) < 0.03:
            ln = re.sub(r"!+", "", ln)
        if ends.get(".", 1) < 0.03:
            ln = re.sub(r"(?<!\.)\.\s*$", "", ln)
        if swaps:
            ln = re.sub(r"\b(" + "|".join(map(re.escape, swaps)) + r")\b",
                        lambda m: swaps[m.group().lower()] if m.group().islower() else swaps[m.group().lower()].capitalize(),
                        ln, flags=re.I)
        ln = re.sub(r"\s{2,}", " ", ln).strip()
        if ln:
            out.append(ln)
    return out
