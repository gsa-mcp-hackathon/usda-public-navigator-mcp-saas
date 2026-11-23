# Contributing

How to make a change that fits this repository.

1. **Look first.** [docs/WHERE-TO-LOOK.md](docs/WHERE-TO-LOOK.md) tells you which folder holds what, and each folder's `README.md` lists its items.
2. **Keep the change small and clear.** One purpose per change, with a short description of what it does and why.
3. **Follow the repository standard.** [docs/REPOSITORY-STANDARD.md](docs/REPOSITORY-STANDARD.md) is short. The main habits: name files for what they are, add a line to the folder index when you add a file, and do not leave one-file folders.
4. **Run the checks before you push.**
   ```bash
   python scripts/check_repo.py
   PYTHONPATH=src python -m pytest -q tests
   ```
   Code style is set in `pyproject.toml` and checked with `ruff`.
5. **Keep data synthetic.** Test and example data is made up. Do not add real personal information, credentials or tokens.
6. **Update the docs with the code.** If behavior changes, change the guide that describes it in the same change.
