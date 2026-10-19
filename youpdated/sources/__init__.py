"""Built-in sources. Importing this package registers all"""

from . import browser, generic, git, homebrew, itch, npm, steam, youtube  # noqa: F401

__all__ = ["browser", "generic", "git", "homebrew", "itch", "npm", "steam", "youtube"]
