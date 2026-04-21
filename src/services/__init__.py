"""Application services — feature orchestration extracted from the GhostAPI facade.

Each service owns one feature area and is instantiated once per GhostAPI.
Services may depend on infra / integrations / platform / domain — never on
`api.py` itself. Services receive the pywebview window handles and HWND
references via their constructors (or a shared context).
"""
