#!/bin/bash
set -euo pipefail

# ══════════════════════════════════════════════════════════════
# H1 Asset Fetcher — Dependency Installer
# Installs all required tools for fetch + download + decompile
# ══════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="${TOOLS_DIR:-$HOME/.local/share/h1-tools}"
BIN_DIR="$HOME/.local/bin"

RED='\033[91m'
GREEN='\033[92m'
YELLOW='\033[93m'
CYAN='\033[96m'
RESET='\033[0m'

log()  { echo -e "${CYAN}[*]${RESET} $1"; }
ok()   { echo -e "${GREEN}[✓]${RESET} $1"; }
warn() { echo -e "${YELLOW}[!]${RESET} $1"; }
err()  { echo -e "${RED}[✗]${RESET} $1"; }

check_cmd() { command -v "$1" &>/dev/null; }

echo ""
echo -e "  ${CYAN}╔════════════════════════════════════════════════════════════╗${RESET}"
echo -e "  ${CYAN}║${YELLOW}     H1 Asset Fetcher — Dependency Installer               ${CYAN}║${RESET}"
echo -e "  ${CYAN}║${RESET}  Installs: python deps, apkeep, jadx, apktool,             ${CYAN}║${RESET}"
echo -e "  ${CYAN}║${RESET}            dex2jar, procyon                                 ${CYAN}║${RESET}"
echo -e "  ${CYAN}╚════════════════════════════════════════════════════════════╝${RESET}"
echo ""

mkdir -p "$TOOLS_DIR" "$BIN_DIR"

# ── Detect OS & arch ─────────────────────────────────────────
ARCH=$(uname -m)
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

case "$ARCH" in
    x86_64)  ARCH_RUST="x86_64"; ARCH_ALT="amd64" ;;
    aarch64) ARCH_RUST="aarch64"; ARCH_ALT="arm64" ;;
    armv7l)  ARCH_RUST="armv7"; ARCH_ALT="armhf" ;;
    *)       err "Unsupported architecture: $ARCH"; exit 1 ;;
esac

# ── System packages ──────────────────────────────────────────
log "Installing system dependencies..."
if check_cmd apt-get; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip unzip curl wget openjdk-17-jre-headless git >/dev/null 2>&1 || \
    sudo apt-get install -y -qq python3 python3-pip unzip curl wget default-jre git >/dev/null 2>&1
    ok "System packages installed"
elif check_cmd dnf; then
    sudo dnf install -y python3 python3-pip unzip curl wget java-17-openjdk-headless git >/dev/null 2>&1
    ok "System packages installed"
elif check_cmd pacman; then
    sudo pacman -Sy --noconfirm python python-pip unzip curl wget jdk17-openjdk git >/dev/null 2>&1
    ok "System packages installed"
elif check_cmd brew; then
    brew install python3 unzip curl wget openjdk@17 git >/dev/null 2>&1
    ok "System packages installed"
else
    warn "Unknown package manager — install manually: python3, pip, java 17+, unzip, curl, wget, git"
fi

# ── Python dependencies ──────────────────────────────────────
log "Installing Python dependencies..."
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    pip3 install --break-system-packages -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || \
    pip3 install -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || \
    pip install -r "$SCRIPT_DIR/requirements.txt"
    ok "Python packages installed (requests, telethon)"
else
    pip3 install --break-system-packages requests telethon 2>/dev/null || \
    pip3 install requests telethon
    ok "Python packages installed"
fi

# ── apkeep ───────────────────────────────────────────────────
if check_cmd apkeep || [ -f "$BIN_DIR/apkeep" ]; then
    ok "apkeep already installed ($(apkeep --version 2>/dev/null || echo 'found'))"
else
    log "Installing apkeep..."
    APKEEP_VERSION=$(curl -sL "https://api.github.com/repos/EFForg/apkeep/releases/latest" | grep '"tag_name"' | head -1 | cut -d'"' -f4 || echo "")

    if [ -n "$APKEEP_VERSION" ]; then
        APKEEP_URL="https://github.com/EFForg/apkeep/releases/download/${APKEEP_VERSION}/apkeep-${ARCH_RUST}-unknown-${OS}-gnu"
        if curl -sL -o "$BIN_DIR/apkeep" "$APKEEP_URL" && [ -s "$BIN_DIR/apkeep" ]; then
            chmod +x "$BIN_DIR/apkeep"
            ok "apkeep ${APKEEP_VERSION} installed -> $BIN_DIR/apkeep"
        else
            rm -f "$BIN_DIR/apkeep"
            warn "apkeep binary download failed — trying cargo..."
            if check_cmd cargo; then
                cargo install apkeep 2>/dev/null && ok "apkeep installed via cargo" || err "apkeep cargo install failed"
            else
                err "apkeep install failed. Install manually: https://github.com/EFForg/apkeep/releases"
            fi
        fi
    else
        warn "Could not fetch apkeep release — trying cargo..."
        if check_cmd cargo; then
            cargo install apkeep 2>/dev/null && ok "apkeep installed via cargo" || err "apkeep cargo install failed"
        else
            err "apkeep install failed. Install manually: https://github.com/EFForg/apkeep/releases"
        fi
    fi
fi

# ── jadx ─────────────────────────────────────────────────────
if check_cmd jadx || [ -f "$BIN_DIR/jadx" ]; then
    ok "jadx already installed ($(jadx --version 2>/dev/null || echo 'found'))"
else
    log "Installing jadx..."
    JADX_VERSION=$(curl -sL "https://api.github.com/repos/skylot/jadx/releases/latest" | grep '"tag_name"' | head -1 | cut -d'"' -f4 || echo "")

    if [ -n "$JADX_VERSION" ]; then
        JADX_ZIP="jadx-${JADX_VERSION#v}-no-jre-all.zip"
        JADX_URL="https://github.com/skylot/jadx/releases/download/${JADX_VERSION}/${JADX_ZIP}"
        JADX_DIR="$TOOLS_DIR/jadx"

        mkdir -p "$JADX_DIR"
        if curl -sL -o "/tmp/$JADX_ZIP" "$JADX_URL" && [ -s "/tmp/$JADX_ZIP" ]; then
            unzip -qo "/tmp/$JADX_ZIP" -d "$JADX_DIR"
            rm -f "/tmp/$JADX_ZIP"
            ln -sf "$JADX_DIR/bin/jadx" "$BIN_DIR/jadx"
            chmod +x "$JADX_DIR/bin/jadx"
            ok "jadx ${JADX_VERSION} installed -> $BIN_DIR/jadx"
        else
            # Fallback: try the full zip with JRE
            JADX_ZIP="jadx-${JADX_VERSION#v}.zip"
            JADX_URL="https://github.com/skylot/jadx/releases/download/${JADX_VERSION}/${JADX_ZIP}"
            curl -sL -o "/tmp/$JADX_ZIP" "$JADX_URL"
            if [ -s "/tmp/$JADX_ZIP" ]; then
                unzip -qo "/tmp/$JADX_ZIP" -d "$JADX_DIR"
                rm -f "/tmp/$JADX_ZIP"
                ln -sf "$JADX_DIR/bin/jadx" "$BIN_DIR/jadx"
                chmod +x "$JADX_DIR/bin/jadx"
                ok "jadx ${JADX_VERSION} installed -> $BIN_DIR/jadx"
            else
                err "jadx install failed. Install manually: https://github.com/skylot/jadx/releases"
            fi
        fi
    else
        err "Could not fetch jadx release. Install manually: https://github.com/skylot/jadx/releases"
    fi
fi

# ── apktool ──────────────────────────────────────────────────
if check_cmd apktool || [ -f "$BIN_DIR/apktool" ]; then
    ok "apktool already installed ($(apktool --version 2>/dev/null || echo 'found'))"
else
    log "Installing apktool..."
    APKTOOL_VERSION=$(curl -sL "https://api.github.com/repos/iBotPeaches/Apktool/releases/latest" | grep '"tag_name"' | head -1 | cut -d'"' -f4 || echo "")

    if [ -n "$APKTOOL_VERSION" ]; then
        APKTOOL_JAR="apktool_${APKTOOL_VERSION#v}.jar"
        APKTOOL_URL="https://github.com/iBotPeaches/Apktool/releases/download/${APKTOOL_VERSION}/${APKTOOL_JAR}"

        if curl -sL -o "$TOOLS_DIR/apktool.jar" "$APKTOOL_URL" && [ -s "$TOOLS_DIR/apktool.jar" ]; then
            # Create wrapper script
            cat > "$BIN_DIR/apktool" << 'WRAPPER'
#!/bin/bash
exec java -jar "$HOME/.local/share/h1-tools/apktool.jar" "$@"
WRAPPER
            chmod +x "$BIN_DIR/apktool"
            ok "apktool ${APKTOOL_VERSION} installed -> $BIN_DIR/apktool"
        else
            err "apktool install failed. Install manually: https://apktool.org/"
        fi
    else
        err "Could not fetch apktool release. Install manually: https://apktool.org/"
    fi
fi

# ── dex2jar (optional) ───────────────────────────────────────
if [ -f "$TOOLS_DIR/dex2jar/d2j-dex2jar.sh" ] || check_cmd d2j-dex2jar.sh; then
    ok "dex2jar already installed"
else
    log "Installing dex2jar (optional, for fast decompile)..."
    D2J_VERSION=$(curl -sL "https://api.github.com/repos/pxb1988/dex2jar/releases/latest" | grep '"tag_name"' | head -1 | cut -d'"' -f4 || echo "")

    if [ -n "$D2J_VERSION" ]; then
        D2J_ZIP="dex-tools-v${D2J_VERSION#v}.zip"
        D2J_URL="https://github.com/pxb1988/dex2jar/releases/download/${D2J_VERSION}/${D2J_ZIP}"

        if curl -sL -o "/tmp/$D2J_ZIP" "$D2J_URL" && [ -s "/tmp/$D2J_ZIP" ]; then
            unzip -qo "/tmp/$D2J_ZIP" -d "$TOOLS_DIR/dex2jar-tmp"
            rm -f "/tmp/$D2J_ZIP"
            # Move extracted dir contents into dex2jar/
            mkdir -p "$TOOLS_DIR/dex2jar"
            mv "$TOOLS_DIR"/dex2jar-tmp/*/* "$TOOLS_DIR/dex2jar/" 2>/dev/null || \
            mv "$TOOLS_DIR"/dex2jar-tmp/* "$TOOLS_DIR/dex2jar/" 2>/dev/null
            rm -rf "$TOOLS_DIR/dex2jar-tmp"
            chmod +x "$TOOLS_DIR"/dex2jar/*.sh 2>/dev/null
            ln -sf "$TOOLS_DIR/dex2jar/d2j-dex2jar.sh" "$BIN_DIR/d2j-dex2jar.sh"
            ok "dex2jar ${D2J_VERSION} installed -> $TOOLS_DIR/dex2jar/"
        else
            warn "dex2jar download failed (optional tool, skipping)"
        fi
    else
        warn "Could not fetch dex2jar release (optional tool, skipping)"
    fi
fi

# ── procyon (optional) ───────────────────────────────────────
if [ -f "$TOOLS_DIR/procyon.jar" ]; then
    ok "procyon already installed"
else
    log "Installing procyon decompiler (optional, for fast decompile)..."
    PROCYON_VERSION=$(curl -sL "https://api.github.com/repos/mstrobel/procyon/releases/latest" | grep '"tag_name"' | head -1 | cut -d'"' -f4 || echo "")

    if [ -n "$PROCYON_VERSION" ]; then
        PROCYON_URL="https://github.com/mstrobel/procyon/releases/download/${PROCYON_VERSION}/procyon-decompiler-${PROCYON_VERSION#v}.jar"
        if curl -sL -o "$TOOLS_DIR/procyon.jar" "$PROCYON_URL" && [ -s "$TOOLS_DIR/procyon.jar" ]; then
            ok "procyon ${PROCYON_VERSION} installed -> $TOOLS_DIR/procyon.jar"
        else
            rm -f "$TOOLS_DIR/procyon.jar"
            warn "procyon download failed (optional tool, skipping)"
        fi
    else
        warn "Could not fetch procyon release (optional tool, skipping)"
    fi
fi

# ── Update h1_asset_fetcher/decompile/fast.sh defaults ────────────────────────
# Export env vars so h1_asset_fetcher/decompile/fast.sh can find the tools
SHELL_RC="$HOME/.bashrc"
[ -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.zshrc"

PATH_LINE="export PATH=\"\$HOME/.local/bin:\$PATH\""
D2J_LINE="export D2J=\"$TOOLS_DIR/dex2jar/d2j-dex2jar.sh\""
PROCYON_LINE="export PROCYON=\"$TOOLS_DIR/procyon.jar\""

add_to_rc() {
    if ! grep -qF "$1" "$SHELL_RC" 2>/dev/null; then
        echo "$1" >> "$SHELL_RC"
    fi
}

add_to_rc "$PATH_LINE"
[ -f "$TOOLS_DIR/dex2jar/d2j-dex2jar.sh" ] && add_to_rc "$D2J_LINE"
[ -f "$TOOLS_DIR/procyon.jar" ] && add_to_rc "$PROCYON_LINE"

# Apply to current session
export PATH="$HOME/.local/bin:$PATH"

# ── Verify ───────────────────────────────────────────────────
echo ""
echo -e "  ${CYAN}════════════════════════════════════════════════════════════${RESET}"
echo -e "  ${CYAN}  Installation Summary${RESET}"
echo -e "  ${CYAN}════════════════════════════════════════════════════════════${RESET}"
echo ""

verify() {
    local name="$1" cmd="$2" optional="${3:-}"
    if check_cmd "$cmd" || [ -f "$BIN_DIR/$cmd" ]; then
        local ver
        ver=$("$cmd" --version 2>/dev/null | head -1 || echo "installed")
        echo -e "  ${GREEN}✓${RESET}  $name  ($ver)"
    else
        if [ "$optional" = "optional" ]; then
            echo -e "  ${YELLOW}○${RESET}  $name  (not installed — optional)"
        else
            echo -e "  ${RED}✗${RESET}  $name  (MISSING)"
        fi
    fi
}

verify "Python 3"    "python3"
verify "pip"         "pip3"
verify "Java"        "java"
verify "apkeep"      "apkeep"
verify "jadx"        "jadx"
verify "apktool"     "apktool"
verify "dex2jar"     "d2j-dex2jar.sh" "optional"

if [ -f "$TOOLS_DIR/procyon.jar" ]; then
    echo -e "  ${GREEN}✓${RESET}  procyon  ($TOOLS_DIR/procyon.jar)"
else
    echo -e "  ${YELLOW}○${RESET}  procyon  (not installed — optional)"
fi

echo ""
echo -e "  Tools dir:  ${CYAN}$TOOLS_DIR${RESET}"
echo -e "  Binaries:   ${CYAN}$BIN_DIR${RESET}"
echo ""

# Check if PATH is set
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "Add $BIN_DIR to your PATH:"
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "  Or restart your shell: source $SHELL_RC"
    echo ""
fi

ok "Setup complete! Run: python3 h1-asset-fetcher.py -u <username> -t <token>"
echo ""
