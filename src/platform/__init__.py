"""Platform abstraction — Windows today, macOS planned.

`adapter.py` defines the ABC. `windows/` contains the Win32 implementation
and Windows-specific preflight/cleanup routines. Higher layers go through
`get_platform()` so a future macOS port only needs a new sibling package.
"""
