#!/bin/sh
# Starts Evolution Go on 127.0.0.1:8080, waits for it, then runs the bot on $PORT (PID 1).
# If Evolution Go dies, the bot is stopped too so Render restarts the whole container.
set -e
: "${PORT:=10000}"
export DATABASE_URL="${DATABASE_URL:-$POSTGRES_USERS_DB}"   # bot tables live next to Evolution's

(cd "${EVO_DIR:-/app/evo}" && ./server; echo "Evolution Go exited (see errors above: DB URL? migrations?)"; kill $$) &

i=0
until python -c "import socket; socket.create_connection(('127.0.0.1', 8080), 1)" 2>/dev/null; do
  i=$((i + 1))
  [ "$i" -gt 120 ] && echo "Evolution Go did not open port 8080 in 120s" && exit 1
  sleep 1
done
echo "Evolution Go is up, starting bot on :$PORT"

# Bot calls /instance/connect itself (webhook http://127.0.0.1:$PORT/webhook) once the API answers.
exec uvicorn bot.main:app --host 0.0.0.0 --port "$PORT" --no-access-log
