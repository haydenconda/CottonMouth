TICKER_LABEL = com.cottonmouth.ticker
BACKEND_LABEL = com.cottonmouth.backend
TICKER_DST = $(HOME)/Library/LaunchAgents/$(TICKER_LABEL).plist
BACKEND_DST = $(HOME)/Library/LaunchAgents/$(BACKEND_LABEL).plist
PROJECT_DIR = $(CURDIR)
DOMAIN = gui/$(shell id -u)
UV = $(shell command -v uv 2>/dev/null || echo "$(HOME)/.local/bin/uv")
LOCK_FILE = $(PROJECT_DIR)/cottonmouth.lock

.PHONY: setup install uninstall start stop restart status logs build-ticker deploy-ticker help

setup: ## First-time setup: install deps, build ticker, install services
	@echo "=== Installing Python dependencies ==="
	@$(UV) sync --project "$(PROJECT_DIR)"
	@echo ""
	@echo "=== Building CottonMouth ticker app ==="
	@$(MAKE) build-ticker
	@echo ""
	@echo "=== Creating .env ==="
	@if [ ! -f "$(PROJECT_DIR)/.env" ]; then \
		cp "$(PROJECT_DIR)/.env.example" "$(PROJECT_DIR)/.env"; \
		echo "Created .env from .env.example — edit it with your tokens"; \
	else \
		echo ".env already exists"; \
	fi
	@echo ""
	@echo "=== Done ==="
	@echo "Next steps:"
	@echo "  1. Edit .env with your API tokens"
	@echo "  2. Run 'make install' to auto-start on login"
	@echo "  3. Run 'make start' to start now"

install: ## Install launchd services (auto-start on login)
	@mkdir -p "$(HOME)/Library/LaunchAgents"
	@sed -e 's|__PROJECT_DIR__|$(PROJECT_DIR)|g' \
	     -e 's|__HOME__|$(HOME)|g' \
	     -e 's|__UV__|$(UV)|g' \
	     "$(PROJECT_DIR)/launchd/backend.plist.in" > "$(BACKEND_DST)"
	@sed -e 's|__PROJECT_DIR__|$(PROJECT_DIR)|g' \
	     -e 's|__HOME__|$(HOME)|g' \
	     "$(PROJECT_DIR)/launchd/ticker.plist.in" > "$(TICKER_DST)"
	@launchctl bootout $(DOMAIN)/$(TICKER_LABEL) 2>/dev/null || true
	@launchctl bootout $(DOMAIN)/$(BACKEND_LABEL) 2>/dev/null || true
	@launchctl bootstrap $(DOMAIN) "$(TICKER_DST)"
	@launchctl bootstrap $(DOMAIN) "$(BACKEND_DST)"
	@echo "Installed and loaded launchd services"

uninstall: stop ## Remove launchd services
	@launchctl bootout $(DOMAIN)/$(TICKER_LABEL) 2>/dev/null || true
	@launchctl bootout $(DOMAIN)/$(BACKEND_LABEL) 2>/dev/null || true
	@rm -f "$(TICKER_DST)" "$(BACKEND_DST)"
	@echo "Uninstalled launchd services"

stop: ## Stop backend + ticker
	@ps aux | grep '[p]ython -m src.main' | awk '{print $$2}' | xargs kill 2>/dev/null || true
	@pkill -f 'CondaMon.app/Contents/MacOS/CondaMon' 2>/dev/null || true
	@launchctl kill SIGTERM $(DOMAIN)/$(TICKER_LABEL) 2>/dev/null || true
	@launchctl kill SIGTERM $(DOMAIN)/$(BACKEND_LABEL) 2>/dev/null || true
	@rm -f "$(LOCK_FILE)"
	@echo "Stopped CottonMouth"

start: ## Start backend + ticker
	@if [ -f "$(LOCK_FILE)" ] && kill -0 $$(cat "$(LOCK_FILE)" 2>/dev/null) 2>/dev/null; then \
		echo "Backend already running (PID $$(cat $(LOCK_FILE)))"; \
	else \
		cd "$(PROJECT_DIR)" && nohup bash -c ' \
			echo $$$$ > $(LOCK_FILE); \
			exec $(UV) run --project . python -m src.main \
		' >> cottonmouth.out.log 2>> cottonmouth.err.log & \
		sleep 1; \
		if [ -f "$(LOCK_FILE)" ] && kill -0 $$(cat "$(LOCK_FILE)" 2>/dev/null) 2>/dev/null; then \
			echo "Started backend (PID $$(cat $(LOCK_FILE)))"; \
		else \
			echo "Started backend"; \
		fi; \
	fi
	@launchctl kickstart -k $(DOMAIN)/$(TICKER_LABEL) 2>/dev/null || \
		open "$(PROJECT_DIR)/CondaMon/CondaMon.app"
	@echo "Started CottonMouth"

restart: stop ## Stop + start
	@sleep 1
	@$(MAKE) start

status: ## Show running processes and health
	@echo "=== Backend ==="
	@if [ -f "$(LOCK_FILE)" ]; then \
		PID=$$(cat "$(LOCK_FILE)" 2>/dev/null); \
		if kill -0 "$$PID" 2>/dev/null; then \
			STARTED=$$(ps -p "$$PID" -o lstart= 2>/dev/null); \
			echo "PID: $$PID (started $$STARTED)"; \
		else \
			echo "STALE lock file (process $$PID not running)"; \
		fi; \
	else \
		echo "Not running"; \
	fi
	@echo ""
	@echo "=== Ticker ==="
	@launchctl list $(TICKER_LABEL) 2>/dev/null || echo "Not loaded (run 'make install')"
	@echo ""
	@echo "=== Processes ==="
	@ps aux | grep -E 'python.*src\.main|CondaMon' | grep -v grep || echo "None"
	@echo ""
	@echo "=== Health ==="
	@if [ -f "$(PROJECT_DIR)/health.json" ]; then \
		python3 -m json.tool "$(PROJECT_DIR)/health.json" 2>/dev/null || cat "$(PROJECT_DIR)/health.json"; \
	else \
		echo "No health.json yet"; \
	fi

logs: ## Tail backend logs
	@tail -f "$(PROJECT_DIR)/cottonmouth.err.log"

build-ticker: ## Build the Swift ticker app
	@cd "$(PROJECT_DIR)/CondaMon" && swift build -c release
	@mkdir -p "$(PROJECT_DIR)/CondaMon/CondaMon.app/Contents/MacOS"
	@cp "$(PROJECT_DIR)/CondaMon/.build/release/CondaMon" \
		"$(PROJECT_DIR)/CondaMon/CondaMon.app/Contents/MacOS/CondaMon"
	@codesign --force --sign - "$(PROJECT_DIR)/CondaMon/CondaMon.app"
	@echo "Built CottonMouth ticker app"

deploy-ticker: build-ticker ## Build + relaunch ticker
	@pkill -f 'CondaMon.app/Contents/MacOS/CondaMon' 2>/dev/null; sleep 1
	@launchctl kickstart -k $(DOMAIN)/$(TICKER_LABEL) 2>/dev/null || \
		open "$(PROJECT_DIR)/CondaMon/CondaMon.app"
	@echo "Deployed and launched CottonMouth"

dev: ## Start backend API + web dashboard for development
	@echo "=== Starting CottonMouth backend (API on :8150) ==="
	@cd "$(PROJECT_DIR)" && $(UV) run --project . python -m src.main &
	@echo "=== Starting web dashboard (on :3000) ==="
	@cd "$(PROJECT_DIR)/web" && npm run dev &
	@echo ""
	@echo "Backend API: http://localhost:8150"
	@echo "Dashboard:   http://localhost:3000"
	@echo ""
	@wait

seed: ## Generate sample trace data for testing
	@cd "$(PROJECT_DIR)" && $(UV) run --project . python scripts/seed_traces.py
	@echo "Sample traces written to traces.jsonl"

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'
