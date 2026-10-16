.PHONY: setup build check test-integration dev

setup:
	python manage.py setup

build:
	python manage.py build

check:
	python -m py_compile app.py manage.py
	python -m unittest discover -s tests -v

test-integration:
	python -m unittest discover -s tests -v

dev:
	python manage.py start
