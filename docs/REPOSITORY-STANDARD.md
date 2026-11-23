# Repository standard

> Version 1.0 · Updated 2026-11-21 · Reference, read before adding or moving files  
> [Documentation index](README.md) · [Where to look](WHERE-TO-LOOK.md) · [Naming standard](NAMING-STANDARD.md)

The rules that keep this repository readable as it grows, and the check that enforces them. They are common sense, written down so that a person or an AI agent adding a file does the same thing every time.

## The rules

1. **A folder has to earn its place.** A folder exists because several related files belong together. Do not leave a folder empty or with a single file; put the file one level up, or in a folder that has company. The only exceptions are listed in `scripts/check_repo.py`, each with its reason (a folder a code import requires).
2. **A folder's `README.md` is its index.** It says what the folder is, why it exists, what each item is and in what order to read or use them, with links. A guide, a runbook or a design write-up gets its own file with a descriptive name, not the `README.md`.
3. **Names carry meaning.** A file or folder name says what it is. Where a name is not self-explanatory, the folder index says why to read it and what to read before or after.
4. **Every item is listed.** In the folders whose index promises a per-file list (for example `tests/`, `scripts/` and `src/`), every file is named in the index. Adding a file means adding its line.
5. **Guides have a header and a footer.** A guide opens with a Version and Updated line and a navigation line, and closes with a link line, so a reader always knows where they are and where to go next.
6. **Links resolve.** A link to a file or folder points at something that exists.
7. **The data stays synthetic.** Test and example data is made up. Do not add real personal information, credentials or tokens to any file.
8. **Keep internal host names out.** Names of private machines or environments do not belong in a public file.
9. **Tests belong at the gate, not in the deploy path.** Run the tests before a release. Keep the deploy path short.

## How it is enforced

`scripts/check_repo.py` checks rules 1 to 6 and 8 and prints each problem. Run it before a change is merged: `python scripts/check_repo.py`.

```bash
python scripts/check_repo.py
```

## Adding a file: the short list

1. Put it in the folder where related files already live. If none fits, create a folder only if at least two files belong in it.
2. Name it for what it is.
3. Add a line for it to the folder's `README.md`.
4. For a new guide, add the header and footer and link it from the documentation index.
5. Run `python scripts/check_repo.py` and the tests.

How to contribute a change is in the [contributing guide](../CONTRIBUTING.md). How to report a problem is in the [security note](../SECURITY.md).

---

[Documentation index](README.md) · [Where to look](WHERE-TO-LOOK.md) · [Naming standard](NAMING-STANDARD.md)
