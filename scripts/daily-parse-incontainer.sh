#!/bin/sh
# Ежедневный парсинг внутри контейнера (сервис parser / supercronic).
#
# Расписание: 00:30 Europe/Moscow — парсим ВЧЕРА.
# MetalInfo пропускается (нет Playwright в образе).
#
# Ручной прогон:
#   docker compose run --rm --entrypoint /app/scripts/daily-parse-incontainer.sh \
#     -e NAV_PARSE_DAY=YYYY-MM-DD parser
#
# Логи автозапуска:
#   docker compose logs -f parser
set -eu

cd /app

export TZ="${NAV_TZ:-Europe/Moscow}"
export SYNC_NEWS_TO_SERVER=false

# NAV_PARSE_DAY=YYYY-MM-DD — ручной прогон; иначе вчера по Москве.
if [ -n "${NAV_PARSE_DAY:-}" ]; then
  DAY="$NAV_PARSE_DAY"
else
  DAY="$(
    python3 - <<'PY'
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
except Exception:
    today = datetime.utcnow().date()
print((today - timedelta(days=1)).isoformat())
PY
  )"
fi

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*"
}

log "Ежедневный парсинг за $DAY (TZ=$TZ)"

if ! python3 run_news_parsers.py \
  --since "$DAY" --until "$DAY" \
  --no-save-db \
  --index-rag \
  --exclude-source "MetalInfo Domestic" \
  --exclude-source "MetalInfo Abroad"; then
  log "ОШИБКА: парсинг/индексация за $DAY завершились с ошибкой"
  exit 1
fi

RAG_COUNT="$(
  python3 - <<PY
import os
import sys
day = "$DAY"
url = os.environ.get("DATABASE_URL", "").strip()
if not url:
    print("?")
    sys.exit(0)
try:
    import psycopg
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM rag_news_documents WHERE news_date = %s",
                (day,),
            )
            print(int(cur.fetchone()[0] or 0))
except Exception as exc:
    print("?", file=sys.stderr)
    print(f"RAG count failed: {exc}", file=sys.stderr)
    print("?")
PY
)"

log "Готово за $DAY (парсинг + RAG, новостей в базе: ${RAG_COUNT:-?})"

# Автозапуск: мало новостей — считаем сбоем. Ручной NAV_PARSE_DAY не валим по порогу.
if [ -z "${NAV_PARSE_DAY:-}" ]; then
  case "$RAG_COUNT" in
    ''|'?' ) log "ПРЕДУПРЕЖДЕНИЕ: не удалось прочитать COUNT из RAG" ;;
    * )
      if [ "$RAG_COUNT" -lt 20 ] 2>/dev/null; then
        log "ОШИБКА: в RAG слишком мало новостей за $DAY ($RAG_COUNT < 20)"
        exit 1
      fi
      ;;
  esac
fi

log "OK"
