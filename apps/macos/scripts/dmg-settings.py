# The .dmg's layout, for dmgbuild, which make-release.sh runs with -D app=... -D background=...
# The window and icon positions must match dmg-background.swift.
from __future__ import annotations

import os

app = defines["app"]  # noqa: F821

format = "ULFO"  # LZFSE
filesystem = "APFS"

files = [app]
# No hide_extensions: it sets Finder info on the bundle, which breaks `codesign --verify --strict`
symlinks = {"Applications": "/Applications"}

background = defines["background"]  # noqa: F821
window_rect = ((200, 120), (640, 400))
default_view = "icon-view"
show_toolbar = False
show_status_bar = False
show_pathbar = False
show_sidebar = False
show_tab_view = False
icon_size = 128
text_size = 13
arrange_by = None
icon_locations = {
    os.path.basename(app): (170, 165),
    "Applications": (470, 165),
}
