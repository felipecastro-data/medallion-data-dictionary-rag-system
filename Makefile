PYTHON ?= python3
# Not .venv: iCloud-synced folders hide dot-folders, which breaks editable installs.
VENV ?= venv
BIN := $(VENV)/bin
INSTALLED := $(BIN)/medallion-index

.PHONY: up refresh

## Install, build the dictionary and index, then launch the chatbot.
up: refresh
	$(BIN)/medallion-chat

## Check for schema drift, then describe and embed only what changed. Stops at the first failure.
refresh: .env $(INSTALLED)
	$(BIN)/medallion-schema-drift
	$(BIN)/medallion-describe
	$(BIN)/medallion-index

$(INSTALLED): pyproject.toml requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -e .
	touch $@

.env:
	@echo "Missing .env: run 'cp .env.example .env' and fill in your credentials." >&2
	@exit 1
