# Stage 1: build Evolution Go from source (needs CGO for its bundled libwebp)
FROM golang:1.25-alpine AS evo
RUN apk add --no-cache git build-base libjpeg-turbo-dev libwebp-dev
ARG EVO_REF=0.7.2
RUN git clone --depth 1 --branch ${EVO_REF} https://github.com/evolution-foundation/evolution-go /src
WORKDIR /src
# CockroachDB fix: with `uniqueIndex`, gorm drops a non-existent "uni_runtime_configs_key" constraint on every start
# after the first (fatal). `unique` keeps the same guarantee and restarts cleanly. Build fails if upstream changes.
RUN grep -q 'gorm:"uniqueIndex;size:100;not null" json:"key"' pkg/core/c0.go \
 && sed -i 's/gorm:"uniqueIndex;size:100;not null" json:"key"/gorm:"unique;size:100;not null" json:"key"/' pkg/core/c0.go
RUN go mod download && CGO_ENABLED=1 go build -ldflags "-X main.version=${EVO_REF}" -o /out/server ./cmd/evolution-go

# Stage 2: Python bot + Evolution Go in one container (Render free = one service)
FROM python:3.12-alpine
# ponytail: no ffmpeg/poppler, the bot only sends text; add them if you ever send audio/PDF media
RUN apk add --no-cache tzdata ca-certificates libjpeg-turbo libwebp
WORKDIR /app
COPY --from=evo /out/server /app/evo/server
COPY --from=evo /src/manager/dist /app/evo/manager/dist
COPY --from=evo /src/VERSION /app/evo/VERSION
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
COPY start.sh .

# Evolution Go defaults (secrets come from Render env vars)
ENV SERVER_PORT=8080 CLIENT_NAME=evolution OS_NAME="Evolution GO" CONNECT_ON_STARTUP=true \
    DATABASE_SAVE_MESSAGES=false EVENT_IGNORE_GROUP=false EVENT_IGNORE_STATUS=true WEBHOOK_FILES=false \
    MINIO_ENABLED=false AMQP_GLOBAL_ENABLED=false NATS_GLOBAL_ENABLED=false LOGTYPE=console WADEBUG=INFO \
    QRCODE_MAX_COUNT=10 TZ=Asia/Kolkata \
    PGSSLROOTCERT=/etc/ssl/certs/ca-certificates.crt PYTHONUNBUFFERED=1

CMD ["sh", "start.sh"]
