.PHONY: setup build check test-integration test-contract test-e2e dev

setup:
	python manage.py setup

build:
	python manage.py build

check:
	python -m py_compile app.py manage.py
	python -m unittest discover -s tests -v

test-integration:
	python -m unittest discover -s tests -v

test-contract:
	python manage.py test-contract

test-e2e:
	python tests/e2e/run_main_scenario.py

dev:
	python manage.py start
