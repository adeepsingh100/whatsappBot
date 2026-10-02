"""Pure decision logic: parse webhook events, filters, safe-mode detection, timing, reply cleanup."""
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_AGE = 30 * 60          # skip messages older than this (overnight backlog)
QUIET_FOR = 30 * 60        # stay quiet in a chat after I type there myself
BATCH_WAIT = 8             # wait for more messages before replying
COMMANDS = {"/on", "/off", "/status"}

# ponytail: keyword list, misses paraphrases; the LLM also gets SAFE_RULE so it can catch the rest
SENSITIVE = re.compile(
    r"(₹|\brs\.?\s?\d|\binr\b|\d+\s?(k|rs|rupees?|lakh|lac)\b|\b(paise?|paisa|pese|money|cash|pay|payment|paid|"
    r"upi|gpay|paytm|phonepe|transfer|bank|loan|udh?aa?r|borrow|lend|rent|fees?|emi|salary|invest|"
    r"promise|pakka|confirm|commit|guarantee|vaada|wada|"
    r"plan|meet|milte|milo|milna|aaja|aaoge|aayega|aa\s?raha|party|trip|tonight|tomorrow|weekend|"
    r"died|death|passed away|accident|hospital|icu|cancer|funeral|serious|emergency|"
    r"expired|guzar|nahi rahe|breakup|divorce|fired|lost (my|his|her) job)\b)",
    re.I,
)
AI_LEAK = re.compile(r"\b(as an ai|an ai\b|language model|i('| a)m (just )?a bot|chatbot|assistant)\b", re.I)


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
    text = msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text")
    return Incoming(id=info["ID"], chat=info["Chat"], sender=info.get("Sender", ""),
                    from_me=bool(info.get("IsFromMe")), is_group=bool(info.get("IsGroup")),
                    ts=_ts(info.get("Timestamp")), text=text.strip() if text else None,
                    push_name=info.get("PushName") or "", sender_alt=info.get("SenderAlt") or "")


def is_direct_chat(m: Incoming) -> bool:
    """1-to-1 chat with a person: not group, status, broadcast list or channel."""
    return not m.is_group and m.chat.endswith(("@s.whatsapp.net", "@lid"))


def is_self_chat(m: Incoming, my_number: str = "") -> bool:
    """The 'Message yourself' chat: I sent it, into a chat whose user is me."""
    if not m.from_me:
        return False
    me = {user(m.sender), user(m.sender_alt), re.sub(r"\D", "", my_number)} - {""}
    return user(m.chat) in me


def too_old(m: Incoming, now: float) -> bool:
    return m.ts is not None and now - m.ts > MAX_AGE


def command(text: str | None) -> str | None:
    t = (text or "").strip().lower()
    return t if t in COMMANDS else None


def is_sensitive(texts: list[str]) -> bool:
    return any(SENSITIVE.search(t) for t in texts)


def human_delay(reply: str, rng: random.Random = random) -> float:
    """15-90 s: some 'reading' time plus ~typing speed, with jitter."""
    return max(15.0, min(90.0, rng.uniform(8, 25) + len(reply) * rng.uniform(0.25, 0.5)))


def clean_reply(text: str) -> list[str]:
    """LLM output -> list of WhatsApp messages to send. Empty list = don't send."""
    text = re.sub(r"^\s*(\[?me\]?|you)\s*:\s*", "", text.strip(), flags=re.I)
    text = text.strip().strip('"“”').strip()
    if not text or AI_LEAK.search(text):
        return []
    lines = [re.sub(r"^\s*(\[?me\]?)\s*:\s*", "", ln, flags=re.I).strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln][:4]
