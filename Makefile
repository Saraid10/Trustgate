.PHONY: check-slice-1 check-slice-2 check-slice-3 scenario-tier-a verify-migrations mutation verify verify-optimized

check-slice-1:
	ruff check .
	mypy api
	pytest -v

check-slice-2:
	docker compose exec api alembic upgrade head
	pytest tests/test_domain_constraints.py -v

check-slice-3:
	pytest tests/test_state_machine.py -v --hypothesis-seed=random

scenario-tier-a:
	for i in 1 2 3; do pytest tests/test_scenarios_tier_a.py -v || exit 1; done

verify-migrations:
	docker compose exec api alembic check

# Breaks each safety invariant on purpose and requires its guarding tests to fail.
mutation:
	python -m scenarios.mutation
	$(MAKE) verify-optimized

# The full local gate, in the order CI runs it. Each step is its own recipe line, so make stops on
# the first non-zero exit rather than reporting the last one.
#
# This exists because a chain like `pytest -q | tail -2 && git commit` does not do what it looks
# like it does: the pipeline's exit code is tail's, which is always zero, so a failing suite lets
# the commit through. Reading the printed summary is not a substitute for an exit code.
verify:
	python -m ruff check .
	python -m ruff format --check .
	python -m mypy
	python -m alembic check
	python -m pytest -q
	python -m scenarios.mutation

# Runs the safety scenarios with assertions stripped.
#
# `python -O` removes assert statements at compile time. A harness that enforced its checks with
# assert would report every scenario as passing while verifying nothing, which is why
# ScenarioViolation is raised explicitly. That reasoning was untested until this target existed:
# replacing the harness's raises with asserts makes this run print DID NOT RAISE on every case.
verify-optimized:
	python -O -m pytest tests/test_scenario_harness.py tests/test_scenarios_tier_a.py -q
