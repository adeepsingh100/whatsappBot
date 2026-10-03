import asyncio

from fastapi.testclient import TestClient

from bot import main
from bot.db import DB
from tests.test_rules import evt

ME = "919811111111@s.whatsapp.net"
RAHUL = "919800000001@s.whatsapp.net"


def setup(monkeypatch, tmp_path, reply="haan bhai"):
    sent, prompts = [], []

    async def fake_evo(path, body=None, **kw):
        if path == "/send/text":
            sent.append((body["number"], body["text"]))
            return {"data": {"Info": {"ID": f"BOT{len(sent)}"}}}
        return {}

    async def fake_llm(msgs, **kw):
        prompts.append(msgs)
        return reply

    monkeypatch.setattr(main, "db", DB(url="", sqlite_path=str(tmp_path / "bot.db")))
    monkeypatch.setattr(main, "S", main.State())
    monkeypatch.setattr(main, "evo", fake_evo)
    monkeypatch.setattr(main.llm, "complete", fake_llm)
    monkeypatch.setattr(main, "BATCH_WAIT", 0.05)
    monkeypatch.setattr(main, "human_delay", lambda text: 0.01)
    return sent, prompts


def run(*payloads, wait=0.3):
    async def go():
        for p in payloads:
            await main.handle(p)
        await asyncio.sleep(wait)
    asyncio.run(go())


def cmd(text, mid):
    return evt(text, chat=ME, sender=ME, from_me=True, mid=mid)


def now_ts():
    import time
    return time.time()


def test_switch_commands(monkeypatch, tmp_path):
    sent, _ = setup(monkeypatch, tmp_path)
    run(cmd("/status", "c1"), cmd("/on", "c2"))
    assert sent == [(ME, "🤖 bot OFF · 0 replies in last 24h"), (ME, "🤖 bot ON · 0 replies in last 24h")]
    assert DB(url="", sqlite_path=str(tmp_path / "bot.db")).is_on()          # survives restart
    run(cmd("/off", "c3"))
    assert sent[-1] == (ME, "🤖 bot OFF · 0 replies in last 24h") and not main.db.is_on()
    run(evt("/on", sender=ME, from_me=True, mid="c4"))                         # typed in Rahul's chat: ignored
    assert len(sent) == 3 and not main.db.is_on()


def test_batches_and_replies(monkeypatch, tmp_path):
    sent, prompts = setup(monkeypatch, tmp_path)
    main.db.set("switch", "on")
    t = now_ts()
    run(evt("oye", ts=t, mid="m1"), evt("kya kar raha", ts=t, mid="m2"), evt("kya kar raha", ts=t, mid="m2"))
    assert sent == [(RAHUL, "haan bhai")] and len(prompts) == 1               # one reply, dup ignored
    assert prompts[0][-1] == {"role": "user", "content": "oye\nkya kar raha"}
    assert main.db.replies_since(3600) == 1


def test_skips(monkeypatch, tmp_path):
    sent, _ = setup(monkeypatch, tmp_path)
    run(evt("hi", ts=now_ts(), mid="off1"))                                    # switch off
    main.db.set("switch", "on")
    run(evt("hi", ts=now_ts() - 3600, mid="old"),                               # overnight backlog
        evt("hi", chat="1-2@g.us", group=True, ts=now_ts(), mid="grp"),
        evt(None, ts=now_ts(), mid="media"))
    assert sent == []


def test_quiet_after_i_type(monkeypatch, tmp_path):
    sent, _ = setup(monkeypatch, tmp_path)
    main.db.set("switch", "on")
    run(evt("hi", ts=now_ts(), mid="m1"), evt("aa raha hoon", sender=ME, from_me=True, ts=now_ts(), mid="me1"))
    run(evt("ok", ts=now_ts(), mid="m2"))
    assert sent == []
    run(evt("echo", sender=ME, from_me=True, ts=now_ts(), mid="BOT1"))         # bot's own message: not me
    main.S.quiet.clear()
    run(evt("ok?", ts=now_ts(), mid="m3"))
    assert sent == [(RAHUL, "haan bhai")]


def test_webhook_auth(monkeypatch):
    monkeypatch.setattr(main, "INSTANCE_TOKEN", "tok")
    c = TestClient(main.app)
    assert c.post("/webhook", json={"event": "x", "instanceToken": "nope"}).status_code == 401
    assert c.get("/qr", params={"key": "wrong"}).status_code == 403
    assert c.get("/health").json() == {"ok": True}


def test_replies_page(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(main, "GLOBAL_API_KEY", "k")
    main.db.log_reply("919800000001@s.whatsapp.net", "kal <b>aa</b>?", "haan", "Rahul")
    c = TestClient(main.app)
    assert c.get("/replies", params={"key": "x"}).status_code == 403
    page = c.get("/replies", params={"key": "k"}).text
    assert "Rahul" in page and "+919800000001" in page and "&lt;b&gt;aa" in page and "OFF" in page


def test_ignore_list(monkeypatch, tmp_path):
    sent, _ = setup(monkeypatch, tmp_path)
    main.db.set("switch", "on")
    run(cmd("/ignore 98000 00001", "i1"))
    assert sent[-1] == (ME, "🤖 ignoring: 9800000001")
    run(evt("hi", ts=now_ts(), mid="r1"))
    assert len(sent) == 1                                                      # Rahul ignored
    run(cmd("/ignored", "i2"), cmd("/unignore +919800000001", "i3"), evt("hi?", ts=now_ts(), mid="r2"))
    assert sent[1:] == [(ME, "🤖 ignoring: 9800000001"), (ME, "🤖 ignoring: nobody"), (RAHUL, "haan bhai")]


def test_bot_question_skipped_without_cooldown(monkeypatch, tmp_path):
    sent, _ = setup(monkeypatch, tmp_path)
    main.db.set("switch", "on")
    run(evt("tu bot hai kya?", ts=now_ts(), mid="b1"))
    assert sent == []                                                          # that message: no reply
    run(evt("acha chal kal milte", ts=now_ts(), mid="b2"))
    assert sent == [(RAHUL, "haan bhai")]                                      # next one: normal, no cooldown


def test_ignore_cancels_scheduled_reply(monkeypatch, tmp_path):
    sent, _ = setup(monkeypatch, tmp_path)
    main.db.set("switch", "on")
    monkeypatch.setattr(main, "BATCH_WAIT", 0.2)
    run(evt("hi", ts=now_ts(), mid="s1"), cmd("/ignore 9800000001", "s2"), wait=0.5)
    assert sent == [(ME, "🤖 ignoring: 9800000001")]                          # scheduled reply dropped


GROUP = "120363000000000001@g.us"


def gmsg(text, mid, sender=RAHUL, mentions=(), quoted="", from_me=False):
    p = evt(text, chat=GROUP, sender=sender, group=True, ts=now_ts(), mid=mid, from_me=from_me)
    p["data"]["Message"] = {"extendedTextMessage": {"text": text, "contextInfo": {
        "mentionedJID": list(mentions), "participant": quoted}}}
    return p


def test_groups_reply_only_when_addressed(monkeypatch, tmp_path):
    sent, prompts = setup(monkeypatch, tmp_path)
    main.db.set("switch", "on")
    main.S.me = {"919811111111", "55259384799419"}
    run(gmsg("party kab hai sab log", "g1"))
    assert sent == []                                                          # not talking to me
    run(gmsg("@55259384799419 tu aa raha?", "g2", mentions=["55259384799419@lid"]))
    assert sent == [(GROUP, "haan bhai")]                                      # @mentioned (by LID)
    assert prompts[-1][-1]["content"].endswith("Rahul: tu aa raha?")         # who said it, mention stripped
    assert "GROUP chat" in prompts[-1][0]["content"]
    run(gmsg("sahi baat", "g3", quoted="919811111111@s.whatsapp.net"))
    assert len(sent) == 2                                                      # replied to my message


def test_learns_my_ids_from_my_messages(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    run(gmsg("hello", "m1", sender="777000111:5@lid", from_me=True))
    assert "777000111" in main.S.me and "777000111" in main.db.get("me_ids")


def test_swipe_reply_context_and_multi_answers(monkeypatch, tmp_path):
    sent, prompts = setup(monkeypatch, tmp_path, reply="haan pakka\n7 baje\nok")
    main.db.set("switch", "on")
    main.S.me = {"919811111111"}
    main.S.style = {"profile": {"reply_lines": [1, 0, 0, 0]}}                # normally 1 line only
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0.01)            # gap between sent lines
    p = evt("haan", ts=now_ts(), mid="q1")
    p["data"]["Message"] = {"extendedTextMessage": {"text": "haan", "contextInfo": {
        "participant": "919811111111@s.whatsapp.net", "quotedMessage": {"conversation": "kal chalega?"}}}}
    run(p, evt("kitne baje?", ts=now_ts(), mid="q2"))
    hist = prompts[-1][-1]["content"]
    assert '[replying to my message: "kal chalega?"] haan' in hist and "kitne baje?" in hist
    assert "2 messages in a row" in prompts[-1][0]["content"]
    assert sent == [(RAHUL, "haan pakka"), (RAHUL, "7 baje")]                 # 2 messages in -> up to 2 out
