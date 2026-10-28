# Security policy

Session Vault handles account credentials locally. Never share your Cookie, authorization headers, OAuth client JSON, tokens, real conversations or generated archives in an issue or pull request.

To report a vulnerability, use this repository's **Security → Advisories → Report a vulnerability** private reporting flow. If that flow is unavailable, open an issue asking the maintainer to enable private reporting **without including vulnerability details or secrets**.

Run the application only on your own computer through its loopback address. Do not expose the server to a LAN, public tunnel or hosting provider. Review updates before running them with your credentials.

Current scope: the latest release/main branch. The project is experimental and depends on undocumented ChatGPT web endpoints; compatibility failures are not necessarily security vulnerabilities.
