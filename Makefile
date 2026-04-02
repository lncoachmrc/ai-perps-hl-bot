.PHONY: run test lint

run:
	python main.py

test:
	pytest -q

lint:
	python -m py_compile $(shell find app -name '*.py') main.py
