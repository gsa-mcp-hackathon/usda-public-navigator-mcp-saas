# Security

Practical notes for keeping this repository safe to share.

- **No secrets.** Do not commit passwords, tokens, keys or connection strings. Configuration that needs a secret reads it from a file or environment setting at run time; see `.env.example` for the names only.
- **No real personal data.** All profiles, personas and examples are fictional. If you find something that looks real, report it and do not copy it elsewhere.
- **Reporting a problem.** If you find a security problem, tell the project team directly or open an issue that describes the problem without including the secret or the personal data itself.
- **Operator functions.** The Core's operator tools need a separate credential and refuse to run without it. The acceptance suite checks this (suite S02). Do not weaken that check to make a test pass.
