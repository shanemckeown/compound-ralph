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

# 🔴 WhatsApp must be FRONTMOST or menu clicks silently do nothing — osascript still
# returns the menu-item reference, so it reads as success. Measured 2026-08-11: clicking
# Contact Info while WhatsApp was backgrounded produced no sheet and no error. That is
# this codebase's signature failure mode (a success signal that is a proxy, not the thing),
# so activate first and verify the effect, never the call.
click_menu() {  # click_menu <menu-bar-item> <item-name>
  osascript -e 'tell application "WhatsApp" to activate' >/dev/null 2>&1
  sleep 0.4
  osascript -e "tell application \"System Events\" to tell process \"WhatsApp\"
    click menu item \"$2\" of menu 1 of menu bar item \"$1\" of menu bar 1
  end tell" >/dev/null 2>&1
}

sheet_count() {
  osascript -e 'tell application "System Events" to tell process "WhatsApp" to return count of sheets of window 1' 2>/dev/null | tr -d '[:space:]'
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
  back)
    # 🔴 View > Back does NOT dismiss the Contact Info sheet (verified 2026-08-11 — the sheet
    # survived it). A sheet is dismissed with Escape. Kept as a separate verified step because
    # leaving a sheet open silently changes what a subsequent click lands on, and the
    # neighbours of Export Chat are Clear Chat / Delete Chat / Block.
    running || die "not running"
    osascript -e 'tell application "WhatsApp" to activate' >/dev/null 2>&1; sleep 0.3
    osascript -e 'tell application "System Events" to key code 53' >/dev/null 2>&1   # Escape
    sleep 1
    n=$(sheet_count)
    if [ "$n" != "0" ]; then
      click_menu View "${LTR}Back"; sleep 1; n=$(sheet_count)
    fi
    [ "$n" = "0" ] || die "sheet still open (sheets=$n) — do NOT proceed to click blind"
    echo "closed" ;;

  info)
    # Verify the EFFECT (a sheet exists), never the call. The click returns success either way.
    running || die "not running"
    click_menu "$CHAT_MENU" "${LTR}Contact Info"; sleep 1.5
    n=$(sheet_count)
    [ "$n" = "1" ] || die "Contact Info did not open a sheet (sheets=$n). Is a chat actually selected? Refusing to continue — a blind click here can hit Clear Chat, Delete Chat or Block."
    echo "sheet open" ;;

  shot)
    # 🔴 `screencapture -R` captures a screen REGION, not a window. If anything is stacked
    # over WhatsApp you silently get THAT app's content instead. Discovered 2026-08-11 when a
    # "WhatsApp window" capture returned Shane's terminal, full of unrelated output.
    # `-l<CGWindowID>` would be correct but AXWindowNumber does not exist (-1728), so there is
    # no window id available here.
    # Therefore: bring WhatsApp to the front, VERIFY it is frontmost, and REFUSE otherwise.
    # Never fall back to capturing the whole screen — Shane's display routinely has client
    # records, and a silent wider capture is worse than no capture.
    running || die "not running"
    dest="${2:?usage: whatsapp-nav.sh shot <path.png>}"
    osascript -e 'tell application "WhatsApp" to activate' >/dev/null 2>&1
    sleep 0.8
    front=$(osascript -e 'tell application "System Events" to get name of first process whose frontmost is true' 2>/dev/null)
    [ "$front" = "WhatsApp" ] || die "WhatsApp is not frontmost (it is '$front'). REFUSING to capture — a region grab here would photograph whatever is on top of it."
    bounds=$(osascript -e 'tell application "System Events" to tell process "WhatsApp" to tell window 1
      set p to position
      set s to size
      return (item 1 of p as string) & "," & (item 2 of p as string) & "," & (item 1 of s as string) & "," & (item 2 of s as string)
    end tell' 2>/dev/null)
    [ -n "$bounds" ] || die "could not read window bounds"
    screencapture -x -o -R"$bounds" "$dest" 2>/dev/null
    [ -s "$dest" ] || die "screenshot produced no file — check Screen Recording permission"
    echo "$dest" ;;

  *) sed -n '25,35p' "$0"; exit 2 ;;
esac
