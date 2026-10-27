PYTHON ?= python3

.venv/ready: requirements.txt
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --quiet -r requirements.txt
	touch .venv/ready

test: .venv/ready
	.venv/bin/python -m pytest -q

run: .venv/ready
	.venv/bin/uvicorn portal.app:app --host 127.0.0.1 --port 8000

.PHONY: test run

admin: .venv/ready
	.venv/bin/python -m portal.cli create-staff $(EMAIL) admin

.PHONY: admin
