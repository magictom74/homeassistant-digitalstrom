# Contributing to pydigitalstrom

Thanks for taking the time to contribute. This document covers the basics
of how the project is structured, how to set up a dev environment, and
what makes a good pull request.

## Reporting issues

Please open a GitHub issue with:

- A short, descriptive title.
- The library version (`pip show pydigitalstrom`) and your Python version.
- The dSS firmware version if relevant (`pydigitalstrom-cli health-check`
  will print the dSID — the firmware version is visible via
  `pydigitalstrom-cli raw-get /json/system/version`).
- A minimal reproducer (a few lines of code is ideal).
- Expected vs. actual behaviour.

For protocol bugs (something the library does that the dSS rejects),
please include the URL-decoded request body the library sent and the
response the dSS produced. Anonymise device IDs and zone names if
you'd rather not share real identifiers.

## Development setup

```bash
git clone git@github.com:magictom74/homeassistant-digitalstrom.git
cd homeassistant-digitalstrom

# Install in editable mode with dev extras
pip install -e ".[dev,cli]"
```

Required Python: 3.10+.

## Running checks locally

```bash
# Lint
ruff check pydigitalstrom

# Static type check
mypy --strict pydigitalstrom

# Tests (when present)
pytest
```

A green run of all three is the bar a PR has to clear.

## Coding standards

- **Python 3.10+ syntax** (`X | Y` unions, `list[X]`, `dict[K, V]`).
  No `Union` / `Optional` / `List` / `Dict` from `typing`.
- **`from __future__ import annotations`** in every module.
- **Models are frozen dataclasses with `slots=True`.** Don't introduce
  mutable state into them.
- **All public methods are async.** No sync variants.
- **No raw dict returns from the public API.** Parse into a dataclass.
- **Don't add `try` / `except Exception:` swallowing.** Use one of the
  specific `DssError` subclasses or let the exception propagate.
- **Logging**: use the `[pydss.<module>]` prefix convention in messages.
- **Docstrings**: Google style. Document `Raises:` for non-obvious
  exceptions.

## Pull request checklist

- [ ] `ruff check pydigitalstrom` is clean
- [ ] `mypy --strict pydigitalstrom` is clean
- [ ] `pytest` is green (when there are tests touching your change)
- [ ] You added a CHANGELOG entry under `## [Unreleased]`
- [ ] You updated relevant docs in `docs/` if the public API changed
- [ ] For protocol contributions: included the DevTools capture or
      reasoning that justifies the wire-format change

## Adding support for a new addon save format

The library has been built up entry-by-entry from DevTools captures.
When the dSS web UI's protocol does something new (a different
sub-category, a new container field, etc.) the safest workflow is:

1. Open the dSS web UI's addon page in a browser with DevTools open
   on the Network tab.
2. Perform the action (save / delete / etc.).
3. Find the matching POST to `/json/event/raise`.
4. Copy the "Payload" → "view source" body and URL-decode it.
5. Match the decoded JSON against the relevant `serialize_*_for_save`
   function in `pydigitalstrom/addons/`. Adjust the function to match.
6. Add a round-trip test under your dev setup that creates+updates+deletes
   a sentinel entry (`ZZ_*` name prefix) and cleans up after itself.

When opening the PR, paste the decoded body into the description so
the reviewer can verify against the same evidence.

## Things we'd love help with

- `pytest` suite using `respx` to mock HTTP. The library currently only
  has live integration tests run against a real box.
- A typed read model for `device-sensor-states` (the only sub-category
  without typed accessors today).
- Multi-language string handling for state names (German web UI
  produces umlaut-encoded strings; UI display should normalise these).
- GitHub Actions release workflow that publishes to PyPI on tag.
- Doc site (mkdocs-material or sphinx) auto-published to GitHub Pages.

## Code of Conduct

Be excellent to each other. Disagreements about technical decisions are
welcome and expected; personal attacks are not.

## License

By contributing you agree that your contribution is licensed under the
MIT License of this project.
