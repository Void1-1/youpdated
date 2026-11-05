#!/bin/zsh
set -euo pipefail
macos=${0:A:h:h}
swift test --package-path $macos --scratch-path ${YOUPDATED_BUILD_DIR:-$HOME/Library/Caches/Youpdated/macos-build} "$@"
