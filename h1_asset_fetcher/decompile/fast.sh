#!/bin/bash
# Fast decompile using dex2jar + procyon for packages not yet decompiled
# Runs alongside jadx (which handles 1 at a time)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

TOOLS_DIR="${TOOLS_DIR:-$HOME/.local/share/h1-tools}"
D2J="${D2J:-$TOOLS_DIR/dex2jar/d2j-dex2jar.sh}"
PROCYON="${PROCYON:-$TOOLS_DIR/procyon.jar}"
APKS_DIR="${APKS_DIR:-apks}"
OUT_DIR="${OUT_DIR:-decompiled}"
MAX_PARALLEL="${MAX_PARALLEL:-2}"
TIMEOUT="${TIMEOUT:-300}"  # 5 min per package

# Validate tools
if [ ! -f "$D2J" ]; then
    echo "[ERR] dex2jar not found at: $D2J"
    echo "      Set D2J=/path/to/d2j-dex2jar.sh or run ./install.sh"
    exit 1
fi
if [ ! -f "$PROCYON" ]; then
    echo "[ERR] procyon not found at: $PROCYON"
    echo "      Set PROCYON=/path/to/procyon.jar or run ./install.sh"
    exit 1
fi
if ! command -v java &>/dev/null; then
    echo "[ERR] java not found. Install JRE 11+ or run ./install.sh"
    exit 1
fi

mkdir -p "$OUT_DIR"

decompile_fast() {
    local pkg="$1"
    local apk_dir="$APKS_DIR/$pkg"
    local out_dir="$OUT_DIR/$pkg"
    local tmpdir=""

    # Skip if already decompiled
    if [ -d "$out_dir/sources" ]; then
        echo "[SKIP] $pkg (already done)"
        return 0
    fi

    # Find APK file (exclude split APKs and config APKs)
    local apk=""
    apk=$(find "$apk_dir" -maxdepth 1 -name "*.apk" -not -name "split_*" -not -name "config.*" 2>/dev/null | head -1)

    # Try XAPK/APKS bundle
    if [ -z "$apk" ]; then
        local bundle=""
        bundle=$(find "$apk_dir" -maxdepth 1 \( -name "*.xapk" -o -name "*.apks" \) 2>/dev/null | head -1)
        if [ -n "$bundle" ]; then
            tmpdir=$(mktemp -d "/tmp/xapk_${pkg}_XXXX")
            unzip -qo "$bundle" -d "$tmpdir" 2>/dev/null || true
            # Look for base.apk first, then any non-split APK
            apk=$(find "$tmpdir" -name "base.apk" 2>/dev/null | head -1)
            if [ -z "$apk" ]; then
                apk=$(find "$tmpdir" -name "*.apk" -not -name "split_*" -not -name "config.*" 2>/dev/null | sort | head -1)
            fi
            if [ -z "$apk" ]; then
                rm -rf "$tmpdir"
                echo "[FAIL] $pkg -> no APK in bundle"
                return 0
            fi
        fi
    fi

    if [ -z "$apk" ]; then
        echo "[FAIL] $pkg -> no APK file"
        return 0
    fi

    # Step 1: dex2jar (convert DEX -> JAR)
    local jar="/tmp/d2j_${pkg}.jar"
    if ! timeout 120 "$D2J" -f -o "$jar" "$apk" >/dev/null 2>&1; then
        echo "[FAIL] $pkg -> dex2jar failed"
        rm -f "$jar"
        [ -n "$tmpdir" ] && rm -rf "$tmpdir"
        return 0
    fi

    if [ ! -f "$jar" ] || [ ! -s "$jar" ]; then
        echo "[FAIL] $pkg -> dex2jar produced empty output"
        rm -f "$jar"
        [ -n "$tmpdir" ] && rm -rf "$tmpdir"
        return 0
    fi

    # Step 2: procyon decompile (JAR -> Java source)
    mkdir -p "$out_dir/sources"
    if ! timeout "$TIMEOUT" java -Xmx512m -jar "$PROCYON" "$jar" -o "$out_dir/sources" >/dev/null 2>&1; then
        echo "[WARN] $pkg -> procyon timed out or failed"
    fi

    # Cleanup temp files
    rm -f "$jar"
    [ -n "$tmpdir" ] && rm -rf "$tmpdir"

    # Verify output
    if [ -d "$out_dir/sources" ] && find "$out_dir/sources" -name "*.java" -print -quit | grep -q .; then
        local count
        count=$(find "$out_dir/sources" -name "*.java" | wc -l)
        echo "[OK] $pkg ($count java files)"
        return 0
    else
        echo "[FAIL] $pkg -> procyon produced no output"
        rm -rf "$out_dir"
        return 0
    fi
}

export -f decompile_fast
export D2J PROCYON APKS_DIR OUT_DIR TIMEOUT

# Count packages to process
TODO=0
for dir in "$APKS_DIR"/*/; do
    [ -d "$dir" ] || continue
    pkg=$(basename "$dir")
    [ -d "$OUT_DIR/$pkg/sources" ] && continue
    TODO=$((TODO + 1))
done

echo "[*] $TODO packages to decompile (parallel: $MAX_PARALLEL)"
echo "[*] Tools: dex2jar=$D2J, procyon=$PROCYON"
echo ""

# Process packages in parallel
for dir in "$APKS_DIR"/*/; do
    [ -d "$dir" ] || continue
    pkg=$(basename "$dir")
    [ -d "$OUT_DIR/$pkg/sources" ] && continue
    echo "$pkg"
done | xargs -P "$MAX_PARALLEL" -I{} bash -c 'decompile_fast "$1"' _ {}

echo ""
echo "=== FAST DECOMPILE DONE ==="
DONE=$(find "$OUT_DIR" -maxdepth 2 -name "sources" -type d 2>/dev/null | wc -l)
echo "Decompiled: $DONE packages in $OUT_DIR/"
