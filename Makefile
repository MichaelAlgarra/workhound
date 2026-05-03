.PHONY: install dev run csv setup clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-15s %s\n", $$1, $$2}'

install: ## Install workhound into the active environment
	pip install .

dev: ## Install in editable mode for development
	pip install -e .

run: ## Run a search with the default profile
	workhound

csv: ## Run a search and save to CSV
	workhound --csv

setup: ## Create or edit a search profile
	workhound --setup

clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info workhound/*.egg-info workhound/__pycache__ __pycache__
