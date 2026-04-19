# Security Policy

## Supported versions

Only the latest minor release is supported. Please upgrade to the current version before
reporting issues. You can see the latest release at
https://github.com/userJesus/ghost/releases/latest

| Version     | Supported |
|-------------|-----------|
| 1.x latest  | ✅         |
| < 1.0       | ❌         |

## Reporting a vulnerability

Please **do NOT** open a public GitHub issue for security problems.

Instead, use one of the private channels below:

- **GitHub Security Advisories** (preferred):
  https://github.com/userJesus/ghost/security/advisories/new
- **E-mail**: `contato.jesusoliveira@gmail.com` with subject `[SECURITY] ghost:`
- **LinkedIn DM**: [linkedin.com/in/ojesus](https://www.linkedin.com/in/ojesus)

Please include:

1. A clear description of the vulnerability and affected component.
2. Steps to reproduce (proof-of-concept if possible).
3. Version / commit / OS.
4. Your assessment of the impact.
5. Whether you want credit in the advisory.

## Response SLA (best-effort)

- Acknowledgement: within **72 hours**.
- Preliminary assessment: within **7 days**.
- Fix or mitigation for critical issues: targeted within **30 days**, depending on complexity.

## Scope

In-scope:

- Code execution from crafted OpenAI responses / maliciously-crafted payloads reaching the Python
  bridge (`src/api.py`).
- Path traversal in file handling (`src/history.py`, meeting processor).
- Credential leakage (OpenAI key, logs).
- Update-checker spoofing vectors.

Out-of-scope:

- Vulnerabilities in third-party dependencies (openai, pywebview, etc.) — report upstream.
- Social-engineering attacks that require the user to voluntarily install a modified Ghost.
- Issues that require an attacker already having full local user-level access.

## Attribution

We gratefully credit reporters in the release notes for every patched vulnerability, unless you
prefer anonymity.

— Jesus Oliveira
