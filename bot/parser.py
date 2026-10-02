"""Parse WhatsApp "Export chat -> Without media" .txt files (Android and iOS formats)."""
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Android: 02/10/2026, 10:15 pm - Name: text      iOS: [02/10/26, 10:15:30 PM] Name: text
_DATE, _TIME, _AMPM = r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", r"(\d{1,2}[:.]\d{2}(?:[:.]\d{2})?)", r"\s?([aApP]\.?\s?[mM]\.?)?"
ANDROID = re.compile(rf"^{_DATE},? {_TIME}{_AMPM} - (.*)$")
IOS = re.compile(rf"^\[{_DATE},? {_TIME}{_AMPM}\] (.*)$")
INVISIBLE = {ord(c): None for c in "‎‏‪‬﻿"}
SPACES = {ord(c): " " for c in "  "}
SKIP = re.compile(
    r"^(<media omitted>|<attached: .*>|.*\bomitted|.*\(file attached\)|this message was deleted\.?"
    r"|you deleted this message\.?|null|missed (voice|video) call.*|waiting for this message.*"
    r"|location: https?://\S+|live location shared|poll:.*)$",
    re.I | re.S,
)
EDITED = re.compile(r"\s*<this message was edited>$", re.I)
FILENAME_CONTACT = re.compile(r"WhatsApp Chat (?:with|-) (.+?)(?:\s*\(\d+\))?$", re.I)


@dataclass
class Msg:
    ts: datetime | None
    sender: str
    text: str


@dataclass
class Chat:
    contact: str
    me: str
    msgs: list[Msg]
    is_group: bool


def _ts(date: str, time: str, ampm: str | None) -> datetime | None:
    d, m, y = re.split(r"[./-]", date)
    time = time.replace(".", ":")
    tfmt = "%H:%M:%S" if time.count(":") == 2 else "%H:%M"
    if ampm:
        tfmt = tfmt.replace("%H", "%I") + " %p"
        time += " " + ampm.replace(".", "").replace(" ", "").upper()
    yfmt = "%Y" if len(y) == 4 else "%y"
    for order in ("%d/%m", "%m/%d"):  # day-first wins when ambiguous
        try:
            return datetime.strptime(f"{d}/{m}/{y} {time}", f"{order}/{yfmt} {tfmt}")
        except ValueError:
            pass
    return None


def parse_chat(text: str) -> list[Msg]:
    """Return messages in file order. Multi-line messages joined, system lines and media dropped."""
    raw: list[Msg | None] = []
    for line in text.translate(INVISIBLE).translate(SPACES).splitlines():
        hit = ANDROID.match(line) or IOS.match(line)
        if hit:
            body = hit.group(4)
            if ": " in body:
                sender, msg = body.split(": ", 1)
                raw.append(Msg(_ts(*hit.group(1, 2, 3)), sender.strip(), msg))
            else:
                raw.append(None)  # system line ("Messages are end-to-end encrypted", "X joined", ...)
        elif raw and raw[-1] is not None:
            raw[-1].text += "\n" + line
    out = []
    for m in raw:
        if m is None:
            continue
        m.text = EDITED.sub("", m.text).strip()
        if m.text and not SKIP.match(m.text):
            out.append(m)
    return out


def contact_from_filename(path: Path) -> str | None:
    hit = FILENAME_CONTACT.search(path.stem)
    return hit.group(1).strip() if hit else None


def load_chats(folder: str | Path, my_name: str | None = None) -> list[Chat]:
    """Parse every .txt export in folder and work out which sender is me.

    Order: MY_NAME env > the sender that isn't the contact named in the filename > the sender
    that appears in the most files.
    """
    parsed = [(p, parse_chat(p.read_text(encoding="utf-8", errors="replace")))
              for p in sorted(Path(folder).glob("*.txt"))]
    file_count = Counter(s for _, msgs in parsed for s in {m.sender for m in msgs})
    chats = []
    for path, msgs in parsed:
        senders = Counter(m.sender for m in msgs)
        if not senders:
            continue
        contact = contact_from_filename(path)
        if my_name and my_name in senders:
            me = my_name
        elif contact and contact in senders and len(senders) == 2:
            me = next(s for s in senders if s != contact)
        else:
            me = max(senders, key=lambda s: (file_count[s], senders[s]))
        if me not in senders:
            continue
        is_group = len(senders) > 2
        if not contact:
            others = [s for s in senders if s != me]
            contact = others[0] if len(others) == 1 else path.stem
        chats.append(Chat(contact, me, msgs, is_group))
    return chats
