#!/bin/zsh
# Builds the app with SwiftPM and wraps it in ad-hoc signed Youpdated.app
# No widget, Share extension, or Shortcuts actions
#
#   scripts/make-dev-app.sh            uses a scratch config and history
#   scripts/make-dev-app.sh --real     uses your real config and history
#   scripts/make-dev-app.sh --open     builds, then opens the app
set -euo pipefail

macos=${0:A:h:h}
repo=${macos:h:h}
build=${YOUPDATED_BUILD_DIR:-$HOME/Library/Caches/Youpdated/macos-build}
scratch=$HOME/Library/Caches/Youpdated/dev
real=0
open_it=0
for arg in "$@"; do
  case $arg in
    --real) real=1 ;;
    --open) open_it=1 ;;
    *) print -u2 "unknown option $arg"; exit 2 ;;
  esac
done

bundle_id=$(sed -n 's/^YOUPDATED_BUNDLE_ID *= *//p' $macos/Config/Youpdated.xcconfig).dev
version=$(sed -n 's/^MARKETING_VERSION *= *//p' $macos/Config/Youpdated.xcconfig)

# The engine: $YOUPDATED_PYTHON, the main checkout's .venv, python3
common=$(git -C $repo rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
python=${YOUPDATED_PYTHON:-}
for candidate in $repo/.venv/bin/python ${common:h}/.venv/bin/python; do
  [[ -z $python && -x $candidate ]] && python=$candidate
done
python=${python:-$(command -v python3)}

swift build --package-path $macos --scratch-path $build --product YoupdatedDev
bin=$(swift build --package-path $macos --scratch-path $build --show-bin-path)

app=$build/Youpdated.app
rm -rf $app
mkdir -p $app/Contents/MacOS $app/Contents/Resources
cp $bin/YoupdatedDev $app/Contents/MacOS/Youpdated

plist=$app/Contents/Info.plist
sed -e "s/\$(EXECUTABLE_NAME)/Youpdated/" \
    -e "s/\$(PRODUCT_BUNDLE_IDENTIFIER)/$bundle_id/g" \
    -e "s/\$(MARKETING_VERSION)/$version/" \
    -e "s/\$(CURRENT_PROJECT_VERSION)/1/" \
    -e "s/\$(MACOSX_DEPLOYMENT_TARGET)/15.0/" \
    -e "s/\$(YOUPDATED_APP_GROUP)//" \
    $macos/Support/Info.plist > $plist
/usr/bin/plutil -replace CFBundleName -string "Youpdated Dev" $plist
/usr/bin/plutil -replace NSServices.0.NSMenuItem.default -string "Watch with Youpdated Dev" $plist
/usr/bin/plutil -replace YoupdatedDevelopment -bool true $plist
/usr/bin/plutil -replace YoupdatedEngine -json "{\"command\": [\"$python\", \"-m\", \"youpdated\"], \"pythonPath\": \"$repo\"}" $plist
if (( ! real )); then
  mkdir -p $scratch
  /usr/bin/plutil -replace YoupdatedEngine.config -string $scratch/config.yaml $plist
  /usr/bin/plutil -replace YoupdatedEngine.state -string $scratch/state.sqlite3 $plist
fi
/usr/bin/plutil -lint -s $plist

codesign --force --sign - --timestamp=none $app

print "Built $app"
print "  engine:  $python -m youpdated (PYTHONPATH=$repo)"
if (( real )); then
  print "  data:    your real config and history"
else
  print "  data:    $scratch"
fi
(( open_it )) && open $app
exit 0
