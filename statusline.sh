#!/bin/bash
input=$(cat)

CURRENT_DIR=$(echo "$input" | jq -r '.workspace.current_dir // ""')
MODEL=$(echo "$input" | jq -r '.model.display_name // "?"')
USED_PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)

# Date and time
DATE=$(date "+%y-%m-%d")
TIME=$(date "+%H:%M:%S")

# Git branch
if [ -d "$CURRENT_DIR/.git" ] || git -C "$CURRENT_DIR" rev-parse --git-dir > /dev/null 2>&1; then
    GIT_BRANCH=$(git -C "$CURRENT_DIR" --no-optional-locks rev-parse --abbrev-ref HEAD 2>/dev/null)
    [ -n "$GIT_BRANCH" ] && GIT_INFO=" ($GIT_BRANCH)" || GIT_INFO=""
else
    GIT_INFO=""
fi

# Color context % — soft dark red when >= 70%
RESET=$'\033[0m'
if [ "$USED_PCT" -ge 70 ] 2>/dev/null; then
    CTX_COLOR=$'\033[38;5;131m'
    CTX="${CTX_COLOR}${USED_PCT}%%${RESET}"
else
    CTX="${USED_PCT}%%"
fi

printf "%s %s [%s] %s%s ${CTX} >>" "$DATE" "$TIME" "$MODEL" "$CURRENT_DIR" "$GIT_INFO"
