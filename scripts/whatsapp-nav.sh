#!/usr/bin/env bash
# whatsapp-nav.sh — deterministic navigation primitives for the WhatsApp Mac app.
#
# WHY THIS EXISTS AS A SEPARATE LAYER:
# WhatsApp exposes almost nothing to the Accessibility API. Measured 2026-08-11:
#   window 1 -> buttons:3 groups:1 scrollareas:0 tables:0 uielements:4
#   the 3 buttons are close / full-screen / minimize. That is all.
#   opening Contact Info changes nothing. window title is "WhatsApp", not the chat name.
# So the interior needs vision. BUT the menu bar is fully exposed and clickable, which
# covers navigation — the bulk of the work. Keeping the two apart means the expensive,
# fragile vision step only handles what genuinely cannot be done any other way.
#
# Menu items are clicked BY NAME, not by coordinate, so a WhatsApp UI redesign that would
# break coordinate clicking leaves this working.
#
# 🔴 The Chat menu's items carry an invisible U+200E (left-to-right mark) before the name.
# "Contact Info" will NOT match; $'‎'"Contact Info" will. Same for the menu bar item
# itself. Lose that and every call fails with a confusing "menu item not found".
#
# Usage:
#   whatsapp-nav.sh check            # is WhatsApp running + is accessibility granted?
#   whatsapp-nav.sh chats            # focus the chat list (View > Chats)
#   whatsapp-nav.sh next             # View > Next Chat
#   whatsapp-nav.sh prev             # View > Previous Chat
#   whatsapp-nav.sh info             # Chat > Contact Info  (the panel Export Chat lives in)
#   whatsapp-nav.sh back             # View > Back (close the panel)
#   whatsapp-nav.sh shot <path.png>  # screenshot the WhatsApp window only
set -uo pipefail

LTR=$'‎'          # the invisible mark WhatsApp prefixes its menu item names with
CHAT_MENU="${LTR}Chat" # menu bar item "Chat" is itself prefixed

die() { echo "whatsapp-nav: $*" >&2; exit 1; }

running() { pgrep -x WhatsApp >/dev/null; }

click_menu() {  # click_menu <menu-bar-item> <item-name>
  osascript -e "tell application \"System Events\" to tell process \"WhatsApp\"
    click menu item \"$2\" of menu 1 of menu bar item \"$1\" of menu bar 1
  end tell" >/dev/null 2>&1
}

case "${1:-}" in
  check)
    running || die "WhatsApp is not running"
    # Enumerating the menu bar is the cheapest proof that Accessibility is granted.
    out=$(osascript -e 'tell application "System Events" to tell process "WhatsApp" to get name of menu bar items of menu bar 1' 2>&1)
    case "$out" in
      *"not allowed"*|*"assistive"*|*-1743*)
        die "Accessibility permission not granted. System Settings > Privacy & Security > Accessibility." ;;
      *WhatsApp*) echo "OK — WhatsApp running, accessibility granted. Menus: $out" ;;
      *) die "unexpected menu response: $out" ;;
    esac ;;

  chats) running || die "not running"; click_menu View "${LTR}Chats" || die "View > Chats failed" ;;
  next)  running || die "not running"; click_menu View "${LTR}Next Chat" || die "View > Next Chat failed" ;;
  prev)  running || die "not running"; click_menu View "${LTR}Previous Chat" || die "View > Previous Chat failed" ;;
  back)  running || die "not running"; click_menu View "${LTR}Back" || die "View > Back failed" ;;
  info)  running || die "not running"; click_menu "$CHAT_MENU" "${LTR}Contact Info" || die "Chat > Contact Info failed" ;;

  shot)
    running || die "not running"
    dest="${2:?usage: whatsapp-nav.sh shot <path.png>}"
    # -l<windowid> captures just WhatsApp, so the screenshot carries no other app's content
    # (Shane's screen routinely has client records on it — do not capture the whole desktop).
    wid=$(osascript -e 'tell application "System Events" to tell process "WhatsApp" to get value of attribute "AXWindowNumber" of window 1' 2>/dev/null)
    if [ -n "$wid" ] && [ "$wid" != "missing value" ]; then
      screencapture -x -o -l"$wid" "$dest" 2>/dev/null || screencapture -x -o "$dest"
    else
      osascript -e 'tell application "WhatsApp" to activate' >/dev/null 2>&1
      screencapture -x -o -R"$(osascript -e 'tell application "System Events" to tell process "WhatsApp" to tell window 1
        set p to position
        set s to size
        return (item 1 of p as string) & "," & (item 2 of p as string) & "," & (item 1 of s as string) & "," & (item 2 of s as string)
      end tell' 2>/dev/null)" "$dest" 2>/dev/null || screencapture -x -o "$dest"
    fi
    [ -s "$dest" ] || die "screenshot produced no file — check Screen Recording permission"
    echo "$dest" ;;

  *) sed -n '25,35p' "$0"; exit 2 ;;
esac
