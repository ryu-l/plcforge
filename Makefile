# Development shortcuts.  Everything here also runs in CI.

PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help venv install test lint typecheck check examples cpp clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv:  ## Create the virtual environment
	$(PYTHON) -m venv $(VENV)

install: venv  ## Install plcforge and the development tools
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

test:  ## Run the test suite
	$(BIN)/pytest

lint:  ## Style and common mistakes
	$(BIN)/ruff check .

typecheck:  ## Static type check
	$(BIN)/mypy

check: lint typecheck test  ## Everything CI runs

examples:  ## Translate every example into build/
	$(BIN)/plcforge --strict check examples --summary
	$(BIN)/plcforge convert examples/scl -t python -o build/python --name conveyor
	$(BIN)/plcforge convert examples/scl -t cpp    -o build/cpp    --name conveyor
	$(BIN)/plcforge convert examples/plcopen/MotorStarter.ld.xml  -t st -o build/st
	$(BIN)/plcforge convert examples/plcopen/TankControl.fbd.xml  -t st -o build/st
	$(BIN)/plcforge lift    examples/python/pump_controller.py -o build/st/PumpStation.st
	$(BIN)/plcforge scaffold plant --from examples/scl -o build/plant --force

cpp: examples  ## Compile the generated C++
	c++ -std=c++17 -Wall -Wextra -Werror -O2 -Ibuild/cpp -fsyntax-only build/cpp/conveyor.hpp
	@echo "generated C++ compiles cleanly"

clean:  ## Remove build output and caches
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
