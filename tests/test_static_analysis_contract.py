"""Every shipped package must be type-checked, and the list saying so must stay honest.

mypy is configured with an explicit package list rather than a path, which is the right choice -
it keeps scratch files and notebooks out of the strict run. It also fails silently in the one way
that matters: a package added later is not checked, nothing reports an error, and the gate goes on
saying "Success" about code it never opened.

That is how `demo` arrived unchecked, and how `delegation` would have arrived after it: shipped
code sitting outside a strict type-checker while the gate reported success, and nothing anywhere
would have said so.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NOT_SHIPPED = frozenset({"tests", "alembic"})
"""Directories that are packages but are not the product. Tests are checked by their own run."""


def _configured_packages() -> set[str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(config["tool"]["mypy"]["packages"])


def _packages_on_disk() -> set[str]:
    return {
        path.parent.name
        for path in ROOT.glob("*/__init__.py")
        if path.parent.name not in NOT_SHIPPED and not path.parent.name.startswith(".")
    }


def test_every_shipped_package_is_type_checked() -> None:
    """A package absent from the list is a package the strict gate never opens."""

    unchecked = _packages_on_disk() - _configured_packages()

    assert not unchecked, (
        f"these packages ship but mypy never checks them: {sorted(unchecked)}. "
        "Add them to [tool.mypy] packages in pyproject.toml, or to NOT_SHIPPED with a reason."
    )


def test_the_configured_list_names_only_packages_that_exist() -> None:
    """A stale name is the same failure wearing the other face.

    mypy does not fail on a package it cannot find when others resolve, so a renamed package leaves
    a list entry that quietly checks nothing.
    """

    missing = {
        name
        for name in _configured_packages()
        if not (ROOT / name / "__init__.py").is_file() and not (ROOT / f"{name}.py").is_file()
    }

    assert not missing, f"[tool.mypy] packages names things that are not here: {sorted(missing)}"


def _packaged_patterns() -> set[str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = config["tool"]["setuptools"]["packages"]["find"]["include"]
    return {pattern.removesuffix("*") for pattern in include}


def test_every_shipped_package_is_in_the_distribution() -> None:
    """A package missing here is missing from the wheel, and only from the wheel.

    Editable installs and the Docker mount both put the source on the path regardless, so the
    omission is invisible in development and total in a real install. `delegation` was absent while
    every test passed against it.
    """

    missing = _packages_on_disk() - _packaged_patterns()

    assert not missing, (
        f"these packages ship but setuptools would not include them: {sorted(missing)}. "
        "Add them to [tool.setuptools.packages.find] include in pyproject.toml."
    )


def test_the_distribution_list_names_only_packages_that_exist() -> None:
    """The same staleness in the other direction.

    `console*` sat in the list long after the package it named was gone. Harmless in itself, and
    exactly the kind of entry that makes a list stop being read.
    """

    absent = {name for name in _packaged_patterns() if not (ROOT / name / "__init__.py").is_file()}

    assert not absent, f"packaging names things that are not here: {sorted(absent)}"
