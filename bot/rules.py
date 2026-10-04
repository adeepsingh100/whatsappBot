"""Pure decision logic: parse webhook events, filters, safe-mode detection, timing, reply cleanup."""
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_AGE = 30 * 60          # skip messages older than this (overnight backlog)
QUIET_FOR = 30 * 60        # stay quiet in a chat after I type there myself
BATCH_WAIT = 8             # wait for more messages before replying
COMMANDS = {"/on", "/off", "/status", "/ignore", "/unignore", "/ignored"}

# ponytail: keyword list, misses paraphrases; the LLM also gets SAFE_RULE so it can catch the rest
SENSITIVE = re.compile(
    r"(₹|\brs\.?\s?\d|\binr\b|\d+\s?(k|rs|rupees?|lakh|lac)\b|\b(paise?|paisa|pese|money|cash|pay|payment|paid|"
    r"upi|gpay|paytm|phonepe|transfer|bank|loan|udh?aa?r|borrow|lend|rent|fees?|emi|salary|invest|"
    r"promise|pakka|confirm|commit|guarantee|vaada|wada|"
    r"plan|meet|milte|milo|milna|aaja|aaoge|aayega|aa\s?raha|party|trip|tonight|tomorrow|weekend|"
    r"call|video|selfie|photo|pic|pics|address|location|"
    r"died|death|passed away|accident|hospital|icu|cancer|funeral|serious|emergency|"
    r"expired|guzar|nahi rahe|breakup|divorce|fired|lost (my|his|her) job)\b)",
    re.I,
)
AI_LEAK = re.compile(r"\b(as an ai|an ai|language model|chatbot|assistant|bot|robot|ai|gpt|chatgpt|"
                     r"automated|style copy|tumhari awaaz|tumhari awaz)\b", re.I)
# Gaali / slurs / insults (Hinglish + English). Replies containing these are never sent, and my old messages
# containing them are kept out of the prompt examples.
ABUSE = re.compile(
    r"\b(bc|mc|bkl|bsdk|bhosd\w*|bosd\w*|bhosad\w*|chut|chutiy\w*|chutya\w*|chutia\w*|madar\w*|maadar\w*|behen\s?ch\w*|bhen\s?ch\w*|"
    r"benchod|bhenchod|behenchod|lund\w*|lawd\w*|laud\w*|lode|lodu|loda|gand|gaand\w*|gandu\w*|randi\w*|rand\b|"
    r"harami\w*|haraam\w*|kutt[aeiy]\w*|kamin[ae]\w*|saal[ae]|suar|jhant\w*|tatt[ae]\w*|"
    r"fuck\w*|f\*+k|wtf|stfu|shit\w*|bitch\w*|bastard\w*|asshole\w*|dick\w*|mf|motherf\w*|slut\w*|whore\w*|"
    r"idiot\w*|stupid|moron\w*|retard\w*|pagal\s?k[ae]\s?bacch?[ae]|ullu\s?k[ae]\s?patth?[ae])\b",
    re.I)

# Someone asks/suspects a bot: don't answer that message (no admitting, no denying), keep chatting after.
BOT_SUSPECT = re.compile(r"\b(bot|robot|ai|a\.i\.?|chat\s?gpt|gpt|automated|auto\s?reply|machine|fake)\b", re.I)
# Asked to do assistant work (code, essays...): a busy friend wouldn't, so don't.
CODE = re.compile(r"(<\?php|```|[{};]\s*$|^\s*(def|function|class|import|echo|print)\b)", re.M)
MAX_LINES, MAX_LINE_CHARS = 3, 90


@dataclass
class Incoming:
    id: str
    chat: str
    sender: str
    from_me: bool
    is_group: bool
    ts: float | None
    text: str | None
    push_name: str
    sender_alt: str = ""
    mentions: tuple[str, ...] = ()   # JIDs @mentioned in the message
    quoted: str = ""                 # author JID of the message this one replies to
    quoted_text: str = ""            # text of that message ("[media]" if it had none)


def user(jid: str) -> str:
    """'9198..:12@s.whatsapp.net' -> '9198..'"""
    return jid.split("@")[0].split(":")[0]


def _ts(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    try:  # Go may emit nanoseconds; Python takes at most 6 fractional digits
        value = re.sub(r"(\.\d{6})\d+", r"\1", str(value)).replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def parse_message(payload: dict) -> Incoming | None:
    """Evolution Go 'Message' webhook -> Incoming, or None for anything else."""
    if payload.get("event") != "Message":
        return None
    data = payload.get("data") or {}
    info, msg = data.get("Info") or {}, data.get("Message") or {}
    if not info.get("ID") or not info.get("Chat"):
        return None
    msg = unwrap(msg)
    ext = msg.get("extendedTextMessage") or {}
    text = msg.get("conversation") or ext.get("text")
    ctx = ext.get("contextInfo") or {}
    return Incoming(id=info["ID"], chat=info["Chat"], sender=info.get("Sender", ""),
                    from_me=bool(info.get("IsFromMe")), is_group=bool(info.get("IsGroup")),
                    ts=_ts(info.get("Timestamp")), text=text.strip() if text else None,
                    push_name=info.get("PushName") or "", sender_alt=info.get("SenderAlt") or "",
                    mentions=tuple(ctx.get("mentionedJID") or ctx.get("mentionedJid") or ()),
                    quoted=ctx.get("participant") or "", quoted_text=_quoted_text(ctx.get("quotedMessage")))


WRAPPERS = ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension",
            "documentWithCaptionMessage", "editedMessage", "deviceSentMessage", "botInvokeMessage")


def unwrap(msg: dict) -> dict:
    """Disappearing-message / view-once / edited wrappers keep the real message under .message."""
    for _ in range(4):
        inner = next((msg[k].get("message") for k in WRAPPERS if isinstance(msg.get(k), dict)), None)
        if not inner:
            return msg
        msg = inner
    return msg


def _quoted_text(q: dict | None) -> str:
    if not q:
        return ""
    t = q.get("conversation") or (q.get("extendedTextMessage") or {}).get("text") or \
        next((v.get("caption") for v in q.values() if isinstance(v, dict) and v.get("caption")), None)
    return (t or "[media]").strip()


def with_context(m: Incoming, me: set[str]) -> str:
    """Message text for the model, with the message it replies to (swipe-reply) and mentions cleaned up."""
    text = strip_mentions(m.text or "")
    if m.quoted_text:
        whose = "my" if user(m.quoted) in me else "their"
        text = f'[replying to {whose} message: "{m.quoted_text[:150]}"] {text}'
    return text


def is_direct_chat(m: Incoming) -> bool:
    """1-to-1 chat with a person: not group, status, broadcast list or channel."""
    return not m.is_group and m.chat.endswith(("@s.whatsapp.net", "@lid"))


def is_group_chat(m: Incoming) -> bool:
    return m.chat.endswith("@g.us")


def aimed_at_someone_else(m: Incoming, me: set[str]) -> bool:
    """Group message that @mentions / swipe-replies to other people but not me."""
    targets = {user(j) for j in (*m.mentions, m.quoted) if j}
    return bool(targets) and not targets & me


def strip_mentions(text: str) -> str:
    return re.sub(r"@\d{6,}\s*", "", text).strip()


def is_self_chat(m: Incoming, my_number: str = "") -> bool:
    """The 'Message yourself' chat: I sent it, into a chat whose user is me."""
    if not m.from_me:
        return False
    me = {user(m.sender), user(m.sender_alt), re.sub(r"\D", "", my_number)} - {""}
    return user(m.chat) in me


def too_old(m: Incoming, now: float) -> bool:
    return m.ts is not None and now - m.ts > MAX_AGE


def command(text: str | None) -> tuple[str, str] | None:
    """'/ignore +91 98123 45678' -> ('/ignore', '+91 98123 45678'); not a command -> None."""
    name, _, arg = (text or "").strip().partition(" ")
    return (name.lower(), arg.strip()) if name.lower() in COMMANDS else None


def phone_key(number: str) -> str:
    """Last 10 digits, so '+91 98123-45678' and '9812345678' match."""
    return re.sub(r"\D", "", number)[-10:]


def is_ignored(m: Incoming, ignored: set[str]) -> bool:
    return bool(ignored & {phone_key(user(j)) for j in (m.chat, m.sender, m.sender_alt) if j})


def is_sensitive(texts: list[str]) -> bool:
    return any(SENSITIVE.search(t) for t in texts)


GREETINGS = {"hi", "hii", "hiii", "hello", "hey", "good", "morning", "night", "gn", "gm", "ji", "jii"}


def is_echo(reply: str, incoming: list[str]) -> bool:
    """Reply only repeats their words ('Okay' -> 'Okay', 'Kab aai thi?' -> 'Kab aai thi?'). Greetings back are fine."""
    said = {w for t in incoming for w in re.findall(r"\w+", t.lower())}
    words = set(re.findall(r"\w+", reply.lower()))
    return bool(words) and words <= said and not words <= GREETINGS


def human_delay(reply: str, rng: random.Random = random) -> float:
    """15-90 s: some 'reading' time plus ~typing speed, with jitter."""
    return max(15.0, min(90.0, rng.uniform(8, 25) + len(reply) * rng.uniform(0.25, 0.5)))


def split_reply(text: str) -> tuple[str, str] | None:
    """'UNDERSTANDING: ... REPLY: ...' -> (understanding, reply). None if the model skipped REPLY."""
    m = re.search(r"\bREPLY\s*:\s*(.*)", text, re.S | re.I)
    if not m:
        return None if re.search(r"\bUNDERSTANDING\s*:", text, re.I) else ("", text.strip())
    u = re.search(r"\bUNDERSTANDING\s*:\s*(.*?)\bREPLY\s*:", text, re.S | re.I)
    return (u.group(1).strip() if u else ""), m.group(1).strip()


def clean_reply(text: str) -> list[str]:
    """LLM output -> list of WhatsApp messages to send. Empty list = don't send."""
    text = re.sub(r"^\s*(\[?me\]?|you)\s*:\s*", "", text.strip(), flags=re.I)
    text = text.strip().strip('"“”').strip()
    if not text or AI_LEAK.search(text) or ABUSE.search(text):
        return []
    if CODE.search(text):
        return []
    text = re.sub(r"\[[^\]]*\]|\*[^*\n]{12,}\*", "", text)            # [Selfie: ...], *long stage directions*
    text = re.sub(r"[*_`#]+", "", text).replace("…", " ").replace("...", " ")  # markdown, trailing-off dots
    lines = [re.sub(r"^\s*(\[?me\]?)\s*:\s*", "", ln, flags=re.I).strip() for ln in text.splitlines()]
    out = []
    for ln in filter(None, lines):
        if len(ln) > MAX_LINE_CHARS:  # keep the first sentence only, like a quick text
            ln = re.split(r"(?<=[.?!])\s", ln)[0][:MAX_LINE_CHARS].rsplit(" ", 1)[0]
        out.append(re.sub(r"\s{2,}", " ", ln).strip())
    return [ln for ln in out if ln][:MAX_LINES]
