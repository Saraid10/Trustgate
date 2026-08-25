.PHONY: check-slice-1 check-slice-2 check-slice-3 scenario-tier-a verify-migrations mutation

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
