# Security Policy

## Reporting a Vulnerability

Please report security issues through GitHub private vulnerability reporting once the repository is public. Until then, use a private channel with the maintainer.

Do not open public issues for vulnerabilities that expose credentials, local files, or remote hosts.

## Scope

In scope:

- GridVibe application code
- Local saved-session handling
- Password encryption and storage behavior
- Browser and Socket.IO API behavior

Out of scope:

- SSH servers you connect to
- Third-party CLI agents launched inside terminals
- Operating-system shell configuration

## Security Notes

GridVibe is designed as a local desktop/browser tool, not a public web service. It binds to `127.0.0.1` by default and has no built-in user authentication or multi-user isolation.

The Flask development server is started with `allow_unsafe_werkzeug=True` because Flask-SocketIO uses Werkzeug in this local setup. Do not expose it directly to the internet.

Socket.IO defaults to wildcard CORS for local browser/native-window usage. Tighten `security.cors_origins` in `config.json` if you bind to anything other than localhost.

SSH host keys currently use Paramiko `AutoAddPolicy`, which accepts unknown host keys on first use. This is convenient for a local launcher but weaker than strict host-key verification.

Saved SSH passwords are encrypted with Fernet using `.encryption_key`. On Unix-like systems the key file is chmodded to `0600`; on Windows, Python's Unix-style chmod is not equivalent to Windows ACL hardening.
