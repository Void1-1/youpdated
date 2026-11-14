"""Built-in sources. Importing this package registers all"""

from . import (  # noqa: F401
    browser, generic, git, homebrew, itch, jsondoc, npm, pypi, steam, webpage, youtube
)

__all__ = [
    "browser", "generic", "git", "homebrew", "itch", "jsondoc", "npm", "pypi", "steam", "webpage",
    "youtube",
]
