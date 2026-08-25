"""The one place a row lock is taken, so a lock always means what it appears to mean.

`SELECT ... FOR UPDATE` through the ORM has a trap. The lock is acquired correctly, but the row
Postgres returns is discarded if that object is already in the session's identity map: SQLAlchemy
keeps the attributes it loaded earlier rather than overwriting them. A caller that waits on the
lock therefore waits for real and then decides from the state it read before waiting, which is the
exact state the lock existed to hide. `expire_on_commit=False` removes the only thing that would
otherwise have refreshed it.

That defect was live in the payment state machine and no test objected, because the objects at the
other lock sites happened not to be loaded earlier in the same session. That is an accident of call
order, not a guarantee, and it changes whenever a caller loads a row before locking it.

So locking goes through `locked()` and nowhere else. `tests/test_locking_discipline.py` asserts
this module is the only source of `.with_for_update(`, which makes the rule enforceable rather than
remembered.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select


def locked[T: tuple[Any, ...]](statement: Select[T]) -> Select[T]:
    """Lock the selected rows and decide from the rows the lock actually returned."""

    return statement.with_for_update().execution_options(populate_existing=True)
