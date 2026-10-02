#!/bin/sh
# Starts Evolution Go on 127.0.0.1:8080 in the background and the bot on $PORT right away (Render wants the
# port open quickly; Evolution's first DB checks can take minutes). The bot retries /instance/connect until
# Evolution answers. If Evolution Go dies, the bot is stopped too so Render restarts the whole container.
set -e
: "${PORT:=10000}"
export DATABASE_URL="${DATABASE_URL:-$POSTGRES_USERS_DB}"   # bot tables live next to Evolution's

(cd "${EVO_DIR:-/app/evo}" && ./server; echo "Evolution Go exited (see errors above: DB URL? migrations?)"; kill $$) &

exec uvicorn bot.main:app --host 0.0.0.0 --port "$PORT" --no-access-log
