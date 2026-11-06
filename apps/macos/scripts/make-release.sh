#!/bin/zsh
# Builds the Release app with the frozen engine inside, ad-hoc signed, then its .dmg
# Freeze the engine first: apps/engine/README.md
set -euo pipefail

macos=${0:A:h:h}
repo=${macos:h:h}
engine=$repo/build/engine/dist/youpdated-engine
work=${YOUPDATED_BUILD_DIR:-/tmp/youpdated-release}
source=$work/macos
derived=$work/derived
version=$(sed -n 's/^MARKETING_VERSION *= *//p' $macos/Config/Youpdated.xcconfig)
dmg=$repo/build/Youpdated-$version-macOS.dmg

if [[ ! -x $engine/youpdated-engine ]]; then
  print -u2 "No frozen engine at $engine. See apps/engine/README.md."
  exit 1
fi

app=$derived/Build/Products/Release/Youpdated.app
staging=$(mktemp -d)
cleanup() {
  rm -rf $staging
  # Xcode registers what it builds, which would list this copy in the Services menu too
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u $app 2>/dev/null || true
}
trap cleanup EXIT

# This script changes the built app, so never build over the last one
rm -rf $app

mkdir -p $source
rsync -a --delete --exclude .build --exclude .swiftpm --exclude xcuserdata $macos/ $source/

# The hardened runtime is for notarized apps, and can't load ad-hoc signed code
xcodebuild -quiet -project $source/Youpdated.xcodeproj -scheme Youpdated -configuration Release \
  -destination 'generic/platform=macOS' -derivedDataPath $derived \
  CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual ENABLE_HARDENED_RUNTIME=NO \
  ENABLE_CODE_COVERAGE=NO CLANG_COVERAGE_MAPPING=NO build

# In Resources, not Helpers: everything under Helpers must be signed code, and the engine has data files too, like its certificates
ditto $engine $app/Contents/Resources/youpdated-engine

# Adding the engine broke the app's seal. The engine's own binaries are already signed.
codesign --force --sign - --timestamp=none $app
codesign --verify --deep --strict $app

if leaks=$(grep -rlaF -e $HOME -e $USER $app); then
  print -u2 "These name the person who built them:\n$leaks"
  exit 1
fi

python3 $repo/apps/engine/smoke.py $app/Contents/Resources/youpdated-engine/youpdated-engine --offline

ditto $app $staging/Youpdated.app
ln -s /Applications $staging/Applications
rm -f $dmg
hdiutil create -quiet -volname Youpdated -srcfolder $staging -format UDZO $dmg

print "\n$dmg ($(du -h $dmg | cut -f1))"
