# Contributing

Thanks for your interest! A few things to know before opening a PR or issue.

## Scope and tested hardware

This project has only been tested on a **Nikon D5300**. PRs adding support for
other Nikon bodies (D750, D7500, Z6, Z fc…) or other PTP/IP cameras are very
welcome — please open an issue first so we can discuss the protocol differences
(some bodies need the vendor opcode `NikonGetEvent 0x90C7`, others have
different storage layouts, etc.).

## Running the tests without a camera

All 25 tests fully mock the sockets — you don't need a camera to validate
changes to the PTP/IP layer.

```bash
pip install -e ".[gui,dev]"
pytest
pytest --cov          # coverage report
```

If you change protocol code, please add a test that mocks the new packet
exchange. `tests/test_client.py` has helpers (`_make_packet`, `_data_start`,
`_data_end`, `_response_packet`) that make this easy.

## Coding conventions

- **CLI / core (`nikon_transfer.client`, `transfer`, `cli`)** must remain
  **stdlib-only** — no runtime dependencies. PySide6 and Pillow are GUI-only
  (extra `[gui]` in `pyproject.toml`).
- User-facing strings and logs are in **French** (the original audience is
  French-speaking); code identifiers and docstrings are in **English**.
- Target Python **3.11+**.

## Reporting protocol bugs

If you hit a PTP/IP protocol issue, please run with `--debug` and include the
relevant packet hex dump in the issue — it speeds up diagnosis a lot:

```bash
nikon-transfer --debug 2>&1 | tee debug.log
```

The non-obvious gotchas we've already documented live in `CLAUDE.md` (and the
"PTP/IP gotchas" section of the README). Check there first; if your issue
matches one of them, the fix may already exist.

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
