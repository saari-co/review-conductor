PYTHON ?= python3

.PHONY: check test build
check: test
	$(PYTHON) scripts/check_provenance.py
	git diff --check

test:
	$(PYTHON) tests/test_review_conductor.py
	$(PYTHON) tests/test_review_conductor_activation.py
	$(PYTHON) tests/test_review_conductor_userland.py
	$(PYTHON) tests/test_review_conductor_profiles.py
	$(PYTHON) -m unittest discover -s tests -p 'test_scaffold.py' -v

build:
	$(PYTHON) scripts/build.py
