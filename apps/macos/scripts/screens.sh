#!/bin/zsh
# Renders the window with the contract fixtures, light and dark, into a folder:
# scripts/screens.sh [folder] (default: ~/Library/Caches/Youpdated/screens)
set -euo pipefail
macos=${0:A:h:h}
build=${YOUPDATED_BUILD_DIR:-$HOME/Library/Caches/Youpdated/macos-build}
swift build --package-path $macos --scratch-path $build --product YoupdatedScreens
$build/debug/YoupdatedScreens ${1:-$HOME/Library/Caches/Youpdated/screens}
