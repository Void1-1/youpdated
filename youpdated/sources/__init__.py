"""Built-in sources. Importing this package registers all"""

from . import (  # noqa: F401
    appstore, browser, crates, fdroid, flathub, generic, git, go, gog, homebrew, itch, jsondoc,
    maven, modrinth, npm, nuget, packagist, pypi, rubygems, sparkle, steam, webpage, winget,
    youtube,
)

__all__ = [
    "appstore", "browser", "crates", "fdroid", "flathub", "generic", "git", "go", "gog",
    "homebrew", "itch", "jsondoc", "maven", "modrinth", "npm", "nuget", "packagist", "pypi",
    "rubygems", "sparkle", "steam", "webpage", "winget", "youtube",
]
