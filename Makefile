.PHONY: help install install-dev run run-otel run-otel-env run-prod run-prod-otel format format-check lint clean test test-cov \
	check-runtime \
	jaeger-up jaeger-down jaeger-down-v jaeger-logs jaeger-ps \
	docker-build docker-run docker-stop docker-logs docker-shell docker-rm docker-clean

# Container runtime: prefer Docker if the daemon responds, else Podman.
# `docker info` avoids picking the docker CLI when only the binary is installed
# and the real runtime is Podman (common on macOS).
DOCKER := $(shell docker info >/dev/null 2>&1 && command -v docker)
PODMAN := $(shell command -v podman 2>/dev/null)

ifdef DOCKER
  COMPOSE_CMD := docker compose
  RUN_CMD := docker
else
  ifdef PODMAN
    COMPOSE_CMD := podman compose
    RUN_CMD := podman
  else
    COMPOSE_CMD :=
    RUN_CMD :=
  endif
endif

# Переменные
PYTHON := python
POETRY := poetry
APP := app.main:app
HOST := 0.0.0.0
PORT := 8000
DOCKER_IMAGE := fastapi-template
DOCKER_CONTAINER := fastapi-template
DOCKER_TAG := latest

JAEGER_COMPOSE_FILE := docker/docker-compose.jaeger.yml

# Colors for pretty output (ANSI)
DIM     := \033[2m
BOLD    := \033[1m
RESET   := \033[0m
CYAN    := \033[0;36m
BLUE    := \033[0;34m
MAGENTA := \033[0;35m
GREEN   := \033[0;32m
YELLOW  := \033[1;33m
WHITE   := \033[1;37m

help: ## Показать справку по командам
	@echo ""
	@echo "$(BOLD)$(MAGENTA)  FastAPI Template — Makefile$(RESET)"
	@echo "$(BLUE)  ═══════════════════════════$(RESET)"
	@echo ""
	@echo "$(DIM)  Usage:$(RESET) $(WHITE)make$(RESET) $(CYAN)<target>$(RESET)"
	@echo ""
	@awk '/^#---/ { sub(/^#---[ \t]*/,""); sub(/[ \t]*---[ \t]*$$/,""); printf "$(BOLD)$(CYAN)  %s$(RESET)\n", $$0; next } /^[a-zA-Z0-9_-]+:.*## / { match($$0,/^[a-zA-Z0-9_-]+/); t=substr($$0,RSTART,RLENGTH); match($$0,/## /); d=substr($$0,RSTART+RLENGTH); printf "    $(YELLOW)%-20s$(RESET) $(DIM)%s$(RESET)\n", t, d }' $(MAKEFILE_LIST)
	@printf "    $(YELLOW)%-20s$(RESET) $(DIM)%s$(RESET)\n" "help" "Show this help message"
	@echo ""

#--- Setup ---
install: ## Установить зависимости проекта
	@echo "$(CYAN)Установка зависимостей...$(NC)"
	$(POETRY) install --no-root

install-dev: ## Установить зависимости проекта (включая dev)
	@echo "$(CYAN)Установка зависимостей (включая dev)...$(NC)"
	$(POETRY) install --no-root --with dev

#--- Run ---
run: ## Запустить приложение в режиме разработки (с hot reload)
	@echo "$(CYAN)Запуск приложения в режиме разработки...$(NC)"
	$(POETRY) run uvicorn $(APP) --reload --host $(HOST) --port $(PORT)

run-otel: ## Запустить приложение с OpenTelemetry
	@echo "$(CYAN)Запуск приложения с OpenTelemetry (dev)...$(NC)"
	$(POETRY) run opentelemetry-instrument uvicorn $(APP) --host $(HOST) --port $(PORT)

run-otel-env: ## Запустить OpenTelemetry с автоподгрузкой переменных из .env
	@echo "$(CYAN)Запуск приложения с OpenTelemetry (dev, .env)...$(NC)"
	@set -a; \
	if [ -f .env ]; then . ./.env; else echo "$(YELLOW)Файл .env не найден, запуск без него$(NC)"; fi; \
	set +a; \
	$(POETRY) run opentelemetry-instrument uvicorn $(APP) --host $(HOST) --port $(PORT)

run-prod: ## Запустить приложение в production режиме (с Gunicorn)
	@echo "$(CYAN)Запуск приложения в production режиме...$(NC)"
	$(POETRY) run gunicorn $(APP) -c config/gunicorn_conf.py

run-prod-otel: ## Запустить production с OpenTelemetry (Gunicorn)
	@echo "$(CYAN)Запуск production с OpenTelemetry...$(NC)"
	$(POETRY) run opentelemetry-instrument gunicorn $(APP) -c config/gunicorn_conf.py

#--- Quality ---
format: ## Форматировать код (black + isort)
	@echo "$(CYAN)Форматирование кода...$(NC)"
	$(POETRY) run black app
	$(POETRY) run isort app

format-check: ## Проверить форматирование кода без изменений
	@echo "$(CYAN)Проверка форматирования кода...$(NC)"
	$(POETRY) run black --check app
	$(POETRY) run isort --check-only app

lint: format-check ## Проверить форматирование кода (алиас для format-check)

clean: ## Очистить кэш и временные файлы
	@echo "$(CYAN)Очистка кэша...$(NC)"
	find . -type d -name "__pycache__" -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -r {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -r {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -r {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -r {} + 2>/dev/null || true
	@echo "$(GREEN)Кэш очищен!$(NC)"

test: ## Run tests
	@echo "$(CYAN)Running tests...$(NC)"
	$(POETRY) run pytest

test-cov: ## Run tests with coverage
	@echo "$(CYAN)Running tests with coverage...$(NC)"
	$(POETRY) run pytest --cov=app --cov-report=term-missing

#--- Utilities ---
shell: ## Открыть Python shell с загруженным окружением
	@echo "$(CYAN)Запуск Python shell...$(NC)"
	$(POETRY) run python

update: ## Обновить зависимости
	@echo "$(CYAN)Обновление зависимостей...$(NC)"
	$(POETRY) update

lock: ## Обновить poetry.lock
	@echo "$(CYAN)Обновление poetry.lock...$(NC)"
	$(POETRY) lock --no-update

#--- Runtime / Jaeger ---
check-runtime: ## Показать выбранный container runtime
	@if [ -n "$(RUN_CMD)" ]; then \
		echo "$(CYAN)Container runtime:$(NC) $(GREEN)$(COMPOSE_CMD)$(NC) / $(RUN_CMD)"; \
	else \
		echo "$(YELLOW)Neither Docker (with a running daemon) nor Podman found.$(NC)"; \
		echo "$(YELLOW)Install Docker/Podman or start Docker daemon to run container targets.$(NC)"; \
		exit 1; \
	fi

jaeger-up: check-runtime ## Поднять локальный Jaeger (OTLP :4318, UI :16686)
	@echo "$(CYAN)Запуск Jaeger...$(NC)"
	$(COMPOSE_CMD) -f $(JAEGER_COMPOSE_FILE) up -d
	@echo "$(GREEN)Jaeger UI: http://localhost:16686$(NC)"
	@echo "$(CYAN)OTLP HTTP для OTEL: http://localhost:4318$(NC)"

jaeger-down: ## Остановить Jaeger (том с трейсами сохраняется)
	@echo "$(CYAN)Остановка Jaeger...$(NC)"
	$(COMPOSE_CMD) -f $(JAEGER_COMPOSE_FILE) down
	@echo "$(GREEN)Готово$(NC)"

jaeger-down-v: ## Остановить Jaeger и удалить том с трейсами
	@echo "$(CYAN)Остановка Jaeger и удаление тома с трейсами...$(NC)"
	$(COMPOSE_CMD) -f $(JAEGER_COMPOSE_FILE) down -v
	@echo "$(GREEN)Готово$(NC)"

jaeger-logs: ## Логи контейнера Jaeger
	$(COMPOSE_CMD) -f $(JAEGER_COMPOSE_FILE) logs -f

jaeger-ps: ## Статус Jaeger
	$(COMPOSE_CMD) -f $(JAEGER_COMPOSE_FILE) ps

show-env: ## Показать текущие переменные окружения
	@echo "$(CYAN)Переменные окружения:$(NC)"
	@env | grep -E "(APP_|DEBUG|HOST|PORT|LOG_|OTEL_)" || echo "Переменные окружения не найдены"

#--- Container app lifecycle ---
docker-build: check-runtime ## Собрать образ контейнера
	@echo "$(CYAN)Сборка образа...$(NC)"
	$(RUN_CMD) build -t $(DOCKER_IMAGE):$(DOCKER_TAG) .
	@echo "$(GREEN)Образ: $(DOCKER_IMAGE):$(DOCKER_TAG)$(NC)"

docker-run: check-runtime ## Запустить контейнер приложения
	@echo "$(CYAN)Запуск контейнера...$(NC)"
	@if [ -f .env ]; then \
		$(RUN_CMD) run -d \
			--name $(DOCKER_CONTAINER) \
			-p $(PORT):8000 \
			--env-file .env \
			$(DOCKER_IMAGE):$(DOCKER_TAG); \
	else \
		$(RUN_CMD) run -d \
			--name $(DOCKER_CONTAINER) \
			-p $(PORT):8000 \
			$(DOCKER_IMAGE):$(DOCKER_TAG); \
	fi
	@echo "$(GREEN)Контейнер запущен: $(DOCKER_CONTAINER)$(NC)"
	@echo "$(CYAN)Приложение: http://localhost:$(PORT)$(NC)"

docker-stop: check-runtime ## Остановить контейнер приложения
	@echo "$(CYAN)Остановка контейнера...$(NC)"
	$(RUN_CMD) stop $(DOCKER_CONTAINER) 2>/dev/null || echo "$(YELLOW)Контейнер не запущен$(NC)"
	@echo "$(GREEN)Контейнер остановлен$(NC)"

docker-logs: check-runtime ## Логи контейнера приложения
	@echo "$(CYAN)Логи контейнера:$(NC)"
	$(RUN_CMD) logs -f $(DOCKER_CONTAINER)

docker-shell: check-runtime ## Shell в контейнере приложения
	@echo "$(CYAN)Вход в контейнер...$(NC)"
	$(RUN_CMD) exec -it $(DOCKER_CONTAINER) /bin/bash

docker-rm: check-runtime ## Удалить контейнер приложения
	@echo "$(CYAN)Удаление контейнера...$(NC)"
	$(RUN_CMD) rm $(DOCKER_CONTAINER) 2>/dev/null || echo "$(YELLOW)Контейнер не существует$(NC)"
	@echo "$(GREEN)Контейнер удален$(NC)"

docker-clean: docker-stop docker-rm ## Остановить и удалить контейнер
	@echo "$(GREEN)Контейнер очищен$(NC)"

docker-up: docker-clean docker-build docker-run ## Собрать и запустить контейнер
	@echo "$(GREEN)Контейнер запущен$(NC)"
	@echo "$(CYAN)Приложение: http://localhost:$(PORT)$(NC)"
