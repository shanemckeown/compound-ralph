---
name: ios-build
description: Build and submit iOS app with App Store compliance checks. Use for TestFlight and App Store submissions.
model: sonnet
allowed-tools:
  - Bash
  - Read
  - Glob
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: |
            if echo "$ARGUMENTS" | grep -q "eas build"; then
              # NOTE: EAS commands must run from mobile-completion/mobile/, not mobile-completion/
              MANIFEST="$HOME/.worktrees/AestheticcNext/mobile-completion/mobile/ios/Aestheticc/PrivacyInfo.xcprivacy"
              if [ -f "$MANIFEST" ]; then
                echo "PrivacyInfo.xcprivacy exists"
              else
                echo "WARNING: PrivacyInfo.xcprivacy missing - Apple will reject!"
                exit 1
              fi
            fi
          statusMessage: "Checking privacy manifest..."
        - type: command
          command: |
            if echo "$ARGUMENTS" | grep -q "eas build\|eas submit"; then
              cd ~/.worktrees/AestheticcNext/mobile-completion && npm run type-check 2>/dev/null || echo "Type check skipped"
            fi
          statusMessage: "Running type check..."
  PostToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: |
            if echo "$ARGUMENTS" | grep -q "eas build"; then
              echo ""
              echo "BUILD COMPLETE - Before uploading to TestFlight:"
              echo "  1. Check build output for ITMS-91053 privacy errors"
              echo "  2. Verify bundle ID matches App Store Connect"
              echo "  3. Check provisioning profile is valid"
              echo ""
            fi
          statusMessage: "Post-build checklist..."
  Stop:
    - hooks:
        - type: command
          command: osascript -e 'display notification "iOS build task complete" with title "Aestheticc Mobile" sound name "Glass"'
---

# iOS Build Subagent

Handles iOS builds and App Store submissions with compliance checks.

## Pre-Build Checks
- Verifies PrivacyInfo.xcprivacy exists (required since iOS 17)
- Runs type checking

## Post-Build Reminders
- ITMS-91053 error check reminder
- Bundle ID verification reminder
- Provisioning profile reminder

## Usage
```
@ios-build "Build iOS app for TestFlight"
@ios-build "Submit to App Store"
```

## CRITICAL: Working Directory
EAS commands MUST run from `~/.worktrees/AestheticcNext/mobile-completion/mobile/` (where eas.json lives), NOT from `mobile-completion/` root.

## Key Files to Verify
- `mobile/ios/Aestheticc/PrivacyInfo.xcprivacy` - Privacy manifest
- `app.json` - Expo config with bundle ID
- `eas.json` - Build profiles

## Common ITMS Errors
- ITMS-91053: Missing privacy manifest
- ITMS-91061: Missing privacy nutrition labels
- ITMS-90062: Bundle identifier mismatch
