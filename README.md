# whatsapp-twin

Auto-replies to your personal WhatsApp 1-to-1 chats in your own writing style.

- **WhatsApp**: [Evolution Go](https://github.com/evolution-foundation/evolution-go) (unofficial, WhatsApp Web protocol, QR login). Your number can get banned. You accept that risk.
- **LLM**: NVIDIA (build.nvidia.com), then OpenRouter free models, then local Ollama. On 429, 5xx or a timeout the bot moves to the next one.
- **Hosting**: one Render free Docker service running Evolution Go (on 127.0.0.1:8080) and the Python bot (on `$PORT`). The bot serves `/health`, `/webhook` and `/qr`. Every other path is proxied to Evolution Go, so the manager UI is at `/manager/login`.
- **DB**: CockroachDB free tier holds the WhatsApp session, the license, the on/off switch, the style data and the reply log.

## How it behaves

- It replies only in 1-to-1 chats. It ignores groups, status, broadcast lists, channels and media-only messages.
- It skips messages older than 30 minutes, so the backlog that arrives when Render wakes at 5:00 gets no reply.
- If you type in a chat yourself, the bot stays quiet in that chat for 30 minutes.
- If someone sends several messages, it waits about 8 seconds and answers them all at once. It waits 15–90 seconds (more for longer replies), marks the messages read, shows "typing…" and then sends.
- **Safe mode** (`SAFE_MODE=true`, the default): when a message is about money, plans, promises or bad news, it replies vaguely ("dekhta hoon, baad mein batata hoon") and agrees to nothing.
- It never sends a reply that sounds like "as an AI…". It logs every reply to the DB.
- The only control is in WhatsApp's **Message yourself** chat: `/on`, `/off`, `/status`. The switch is stored in the DB and starts **off**.

## Setup

### 1. CockroachDB

1. Create a free cluster at cockroachlabs.cloud, then create a SQL user and password.
2. Copy the connection string (`postgresql://user:pass@host:26257/defaultdb?sslmode=verify-full`).
3. Make two URLs from it by changing the database name to `evogo_auth` and `evogo_users`. Evolution Go creates both databases on first start.
4. **Add `&options=-c%20autocommit_before_ddl%3Dfalse` to both URLs.** Without it, Evolution Go's WhatsApp-session migrations fail on CockroachDB with `pq: unexpected transaction status idle`. This was tested against CockroachDB v26.3. With it, migrations run and the session survives restarts.

   ```
   postgresql://user:pass@host:26257/evogo_auth?sslmode=verify-full&options=-c%20autocommit_before_ddl%3Dfalse
   ```

   If CockroachDB still causes problems, switch to **Neon** (real Postgres). Paste Neon's two URLs instead and drop the `options=…` part. Nothing else changes.

The bot's own tables (`bot_settings`, `bot_replies`) go into `evogo_users` unless you set `DATABASE_URL`.

### 2. Deploy on Render

1. Push this repo to GitHub. Secrets and chat files are in `.gitignore`.
2. In Render, choose **New → Blueprint** and pick the repo. It reads `render.yaml` and creates one free Docker web service.
3. Fill in the env vars it asks for:

   | var | value |
   |---|---|
   | `POSTGRES_AUTH_DB`, `POSTGRES_USERS_DB` | the two URLs from step 1 |
   | `NVIDIA_API_KEY` | from build.nvidia.com |
   | `OPENROUTER_API_KEY` | optional fallback |
   | `MY_NUMBER` | your number with country code, digits only (e.g. `919811111111`) |
   | `EVOLUTION_OPERATOR_EMAIL` | leave empty for now (see step 3) |

   Render generates `GLOBAL_API_KEY` and `INSTANCE_TOKEN` for you. Copy `GLOBAL_API_KEY` from the Environment tab, because you need it to log in. All other settings have defaults in the `Dockerfile` (see `.env.example`).
4. The first build compiles Evolution Go from source and takes a few minutes. The logs then show `Evolution Go is up, starting bot` followed by `Evolution Go not ready: … 503 … (license not activated?)` every 15 seconds. That is expected until step 3.

### 3. Activate the Evolution Go license (once)

1. Open `https://<your-app>.onrender.com/manager/login`.
2. Follow the free license registration with your email.
3. Once it is activated, set `EVOLUTION_OPERATOR_EMAIL` to that email in Render. Evolution Go can then re-activate itself without the browser if it ever needs to. The license is stored in the DB, so restarts keep it.

### 4. Instance (automatic)

The bot creates an Evolution Go instance named `twin` using your `INSTANCE_TOKEN`, if it doesn't exist yet. It then calls `/instance/connect` with webhook `http://127.0.0.1:$PORT/webhook`. You can see the instance in the manager UI. You don't need to create it by hand.

Auth note, checked in the Evolution Go source: `/instance/create` and `/instance/all` take `apikey: GLOBAL_API_KEY`. `/instance/connect`, `/send/*` and `/message/*` take `apikey: <instance token>`.

### 5. Scan the QR code

1. Open `https://<your-app>.onrender.com/qr?key=<GLOBAL_API_KEY>`. The page refreshes every 10 seconds.
2. On your phone go to **WhatsApp → Settings → Linked devices → Link a device** and scan the code.
3. The logs show `WhatsApp: PairSuccess` and then `Connected`. The session is stored in CockroachDB, so you don't scan again after restarts or nightly sleep.

### 6. Keep it awake 5:00–23:59 IST (cron-job.org)

1. Create a cron job at cron-job.org:
   - URL: `https://<your-app>.onrender.com/health`
   - Schedule: custom, every 5 minutes, hours 5–23
   - Time zone: **Asia/Kolkata**
2. After the last ping (23:55) Render puts the service to sleep about 15 minutes later. That is about 19 hours a day, roughly 570 hours a month, which fits inside the 750 free hours.

Don't point any other service's webhook at this URL, because any incoming request wakes it up.

### 7. Teach it your style

1. On your phone, open a chat and choose **⋮ → More → Export chat → Without media**. Do this for your most active 1-to-1 chats (more data gives better results).
2. Put the `.txt` files in `data/chats/`. Both Android and iOS formats work. The contact name is taken from the filename (`WhatsApp Chat with Rahul.txt`).
3. Run these commands locally:

   ```sh
   python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Python 3.10+ (macOS python3 is 3.9)
   export MY_NAME="Your Name As In Exports"          # optional, auto-detected otherwise
   .venv/bin/python scripts/build_style.py           # writes data/style.json, prints a summary
   ```

4. Try it in the terminal before going live. It uses the same prompt and retrieval as the bot:

   ```sh
   export NVIDIA_API_KEY=...
   .venv/bin/python scripts/try_reply.py Rahul       # type their messages, empty line = your reply
   .venv/bin/python scripts/try_reply.py Rahul --prompt   # also show the system prompt
   ```

5. Upload the style to the DB, which is where the deployed bot reads it from, then restart the Render service (**Manual Deploy → Restart**):

   ```sh
   DATABASE_URL='<your POSTGRES_USERS_DB url>' .venv/bin/python scripts/build_style.py --upload
   ```

### 8. Turn it on

In WhatsApp, open the **Message yourself** chat and send `/on`. The bot answers `🤖 bot ON · 0 replies in last 24h`.

- `/off` stops it immediately.
- `/status` shows whether it is on and how many replies it sent in the last 24 hours.

If commands get no answer, check that `MY_NUMBER` is set. The self-chat can show up under a `@lid` id.

## Development

```sh
.venv/bin/python -m pytest -q          # parser, style, rules, switch commands, webhook flow
```

Without `DATABASE_URL` the bot uses SQLite (`data/bot.db`). Set `OLLAMA_URL=http://localhost:11434` to test with a local model.

| file | what |
|---|---|
| `bot/parser.py` | WhatsApp export parser (Android + iOS) |
| `bot/style.py` | style profile, reply pairs, retrieval, prompt |
| `bot/rules.py` | webhook parsing, filters, safe mode, timing |
| `bot/llm.py` | NVIDIA → OpenRouter → Ollama |
| `bot/db.py` | switch / style / reply log (SQLite or Postgres/CockroachDB) |
| `bot/main.py` | FastAPI app, batching, reverse proxy |
| `start.sh` | starts Evolution Go, waits for port 8080, starts the bot |
