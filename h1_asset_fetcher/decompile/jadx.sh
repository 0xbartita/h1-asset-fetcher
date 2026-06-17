#!/bin/bash
set -euo pipefail
# Batch decompile all APKs with jadx
# Output: decompiled/<package_name>/

# APKS_DIR / OUT_DIR resolve against the CALLER's working directory. Do NOT cd
# into the script's own folder — that breaks relative paths like "apks" (which
# is exactly how the wizard invokes this).
APKS_DIR="${APKS_DIR:-apks}"
OUT_DIR="${OUT_DIR:-decompiled}"
MAX_PARALLEL="${MAX_PARALLEL:-1}"

mkdir -p "$OUT_DIR"

decompile_one() {
    local pkg="$1"
    local pkg_dir="$APKS_DIR/$pkg"
    local out="$OUT_DIR/$pkg"

    # Skip if already decompiled
    if [ -d "$out/sources" ]; then
        echo "[SKIP] $pkg (already done)"
        return
    fi

    # Find APK file
    local apk=$(find "$pkg_dir" -name "*.apk" -not -name "config.*" | head -1)
    local xapk=$(find "$pkg_dir" -name "*.xapk" -o -name "*.apks" | head -1)

    if [ -n "$apk" ]; then
        echo "[JADX] $pkg -> $apk"
        jadx --no-debug-info -q -j 2 "$apk" -d "$out" 2>/dev/null
    elif [ -n "$xapk" ]; then
        # Extract base APK from bundle
        local tmp="/tmp/xapk_$$_$pkg"
        mkdir -p "$tmp"
        unzip -qo "$xapk" -d "$tmp" 2>/dev/null
        local base=$(find "$tmp" -name "base.apk" -o -name "$pkg.apk" | head -1)
        if [ -z "$base" ]; then
            base=$(find "$tmp" -name "*.apk" -not -name "config.*" -not -name "split_*" | sort -r | head -1)
        fi
        if [ -n "$base" ]; then
            echo "[JADX] $pkg -> $base (from bundle)"
            jadx --no-debug-info -q -j 2 "$base" -d "$out" 2>/dev/null
        else
            echo "[FAIL] $pkg -> no APK found in bundle"
        fi
        rm -rf "$tmp"
    else
        echo "[FAIL] $pkg -> no APK file"
    fi
}

export -f decompile_one
export APKS_DIR OUT_DIR

# Get list of packages
find "$APKS_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | \
    xargs -P "$MAX_PARALLEL" -I{} bash -c 'decompile_one "$1"' _ {}

echo ""
echo "=== DONE ==="
echo "Decompiled: $(ls -d $OUT_DIR/*/sources 2>/dev/null | wc -l) packages"
echo "Output: $OUT_DIR/"
