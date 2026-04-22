"""Single source of truth for the Ghost version.

All installers, the py2app bundler, PyInstaller, and the in-app update checker
read from here so a release is a single-line change.
"""

__version__ = "1.1.26"

# GitHub repo used by the update checker to find the latest release.
GITHUB_OWNER = "userJesus"
GITHUB_REPO = "ghost"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_RELEASES_URL = f"{GITHUB_REPO_URL}/releases"
GITHUB_LATEST_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

AUTHOR_NAME = "Jesus Oliveira"
AUTHOR_EMAIL = "contato.jesusoliveira@gmail.com"
AUTHOR_LINKEDIN = "https://www.linkedin.com/in/ojesus"
AUTHOR_GITHUB = "https://github.com/userJesus"
