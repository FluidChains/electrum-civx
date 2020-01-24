#!/usr/bin/env bash

. $(dirname "$0")/../build_tools_util.sh


function DoCodeSignMaybe { # ARGS: infoName fileOrDirName codesignIdentity
    infoName="$1"
    file="$2"
    identity="$3"
    deep=""
    if [ -z "$identity" ]; then
        # we are ok with them not passing anything; master script calls us unconditionally even if no identity is specified
        return
    fi
    if [ -d "$file" ]; then
        deep="--deep"
    fi
    if [ -z "$infoName" ] || [ -z "$file" ] || [ -z "$identity" ] || [ ! -e "$file" ]; then
        fail "Argument error to internal function DoCodeSignMaybe()"
    fi
    info "Code signing ${infoName}..."
    codesign -f -v $deep -s "$identity" "$file" || fail "Could not code sign ${infoName}"
}

function CreateDMG() {   # ARGS: PACKAGE_NAME VERSION DMG_BACKGROUND
    # the background image must be 72 dpi. Coordinates are from the upper-left corner
    windowX=400
    windowY=100
    electrumIconX=140
    electrumIconY=220
    applicationsIconX=470
    applicationsIconY=220

    PACKAGE_NAME="$1"
    VERSION="$2"
    DMG_BACKGROUND="$3"
    imgWidth=`sips -g pixelWidth contrib/osx/$DMG_BACKGROUND | tail -n1 | cut -d ":" -f2 | xargs`
    imgHeight=`sips -g pixelHeight contrib/osx/$DMG_BACKGROUND | tail -n1 | cut -d ":" -f2 | xargs`
    rightX=$(($windowX + $imgWidth))
    rightY=$(($windowY + $imgHeight + 20))

    # set up app name, version number, and background image file name
    APP_EXE="dist/${PACKAGE_NAME}.app/Contents/MacOS/${PACKAGE_NAME}"
    VOL_NAME="${PACKAGE_NAME}-${VERSION}"
    DMG_TMP="dist/${VOL_NAME}-temp.dmg"
    DMG_FINAL="dist/${VOL_NAME}.dmg"
    STAGING_DIR="dist/Staging"

    rm -rf "${STAGING_DIR}" "${DMG_TMP}" "${DMG_FINAL}"

    mkdir -p "${STAGING_DIR}"
    cp -rpf "dist/${PACKAGE_NAME}.app" "${STAGING_DIR}"

    hdiutil create -quiet -srcfolder "${STAGING_DIR}" -volname "${VOL_NAME}" -fs HFS+ \
        -fsargs "-c c=64,a=16,e=16" -format UDRW "${DMG_TMP}" \
        || fail "Could not create .DMG"

    info "Mounting ${VOL_NAME}-temp and save the device"
    DEVICE=$(hdiutil attach -readwrite -noverify "${DMG_TMP}" | \
         egrep '^/dev/' | sed 1q | awk '{print $1}')

    sleep 2

    pushd /Volumes/"${VOL_NAME}"
    ln -s /Applications
    popd

    info "Adding background image and setup icons" 
    mkdir /Volumes/"${VOL_NAME}"/.background
    cp "contrib/osx/${DMG_BACKGROUND}" /Volumes/"${VOL_NAME}"/.background/
    cp "dist/${PACKAGE_NAME}.app/Contents/Resources/exos-electrum.icns" /Volumes/"${VOL_NAME}"/.VolumeIcon.icns
    SetFile -c icnC /Volumes/"${VOL_NAME}"/.VolumeIcon.icns
    SetFile -a C /Volumes/"${VOL_NAME}"

    # Resize the window, set the background, icon size, and icons place
    echo '
    tell application "Finder"
        tell disk "'${VOL_NAME}'"
            open
            set current view of container window to icon view
            set toolbar visible of container window to false
            set statusbar visible of container window to false
            set the bounds of container window to {'$windowX', '$windowY', '$rightX', '$rightY'}
            set viewOptions to the icon view options of container window
            set arrangement of viewOptions to not arranged
            set icon size of viewOptions to 80
            set background picture of viewOptions to file ".background:'${DMG_BACKGROUND}'"
            set position of item "'${PACKAGE_NAME}'.app" of container window to {'$electrumIconX', '$electrumIconY'}
            set position of item "Applications" of container window to {'$applicationsIconX', '$applicationsIconY'}
            close
            open
            update without registering applications
            delay 2
        end tell
    end tell
    ' | osascript
    sync

    hdiutil detach "${DEVICE}" -quiet

    info "Creating compressed disk image"
    hdiutil convert "${DMG_TMP}" -quiet -format UDZO -imagekey zlib-level=9 -o "${DMG_FINAL}" || fail "Could not create .DMG"

    rm -rf "${DMG_TMP}" "${STAGING_DIR}"
}

function CreatePKG() {   # ARGS: PACKAGE_NAME VERSION PKG_BACKGROUND IDENTIFIER APPLE_DEV_INSTALLER_CERT DEV_INSTALLER_CERT
    PACKAGE_NAME="$1"
    VERSION="$2"
    PKG_ICON="$3"
    IDENTIFIER="$4"
    APPLE_DEV_INSTALLER_CERT="$5"
    DEV_INSTALLER_CERT="$6"

    # set up app name, version number, and icon for the pkg file
    PKG_ICON="electrum/gui/icons/${PKG_ICON}" 
    VOL_NAME="${PACKAGE_NAME}-${VERSION}"
    PKG_TMP="dist/${PACKAGE_NAME}.pkg"
    PKG_FINAL="dist/${VOL_NAME}.pkg"    

    mkdir -p dist/build && cp -r dist/${PACKAGE_NAME}.app $_
    pkgbuild --analyze --root dist/build dist/components.plist
    plutil -replace BundleIsRelocatable -bool false dist/components.plist

    pkgbuild --root "dist/build" \
      --component-plist "dist/components.plist" \
      --version ${VERSION} \
      --identifier ${IDENTIFIER} \
      --install-location "/Applications" \
      "${PKG_TMP}"
    
    if [ -z "$DEV_INSTALLER_CERT" ]; then
        productbuild --distribution "contrib/osx/distribution.xml" \
        --package-path "dist" \
        --resources "contrib/osx" \
        "${PKG_FINAL}"
    else
        productbuild --distribution "contrib/osx/distribution.xml" \
        --package-path "dist" \
        --resources "contrib/osx" \
        --sign "$APPLE_DEV_INSTALLER_CERT$DEV_INSTALLER_CERT" \
        "${PKG_FINAL}"
    fi
    
    echo "read 'icns' (-16455) \"${PKG_ICON}\";" > dist/tmpicns.rsrc
    Rez -a dist/tmpicns.rsrc -o ${PKG_FINAL}
    SetFile -a C ${PKG_FINAL}

    rm -rf dist/tmpicns.rsrc dist/components.plist dist/build ${PKG_TMP}
}

function realpath() {
    [[ $1 = /* ]] && echo "$1" || echo "$PWD/${1#./}"
}
