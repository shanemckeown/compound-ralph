---
name: image-pipeline
description: Process images for TikTok packs - face swaps, screen swaps, registry updates. Use for batch image processing.
model: haiku
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
            if echo "$ARGUMENTS" | grep -q "face_swap\|screen_swap\|batch_"; then
              cd /Users/shane/Documents/AestheticcTools/project_files/tiktok-ig
              if [ -d ".venv" ]; then
                echo "Virtual environment ready"
              else
                echo "WARNING: .venv not found - run: python3 -m venv .venv"
                exit 1
              fi
            fi
          statusMessage: "Checking virtual environment..."
  PostToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: |
            if echo "$ARGUMENTS" | grep -q "batch_face_swap\|batch_screen_swap"; then
              echo ""
              echo "Batch processing complete. Next steps:"
              echo "  1. Review output folder for quality"
              echo "  2. Move approved images to *_final folder"
              echo "  3. Run registry rebuild if needed"
              echo ""
            fi
          statusMessage: "Post-processing checklist..."
  Stop:
    - hooks:
        - type: command
          command: osascript -e 'display notification "Image processing complete" with title "AestheticcTools" sound name "Glass"'
---

# Image Pipeline Subagent

Handles batch image processing for TikTok/IG content generation.

## Capabilities
- Face swap processing (FACE → FACE_SWAPPED)
- Screen swap processing (DEVICE → PHONE_SWAPPED)
- Registry management

## Usage
```
@image-pipeline "Process all new FACE images"
@image-pipeline "Run screen swap on DEVICE folder"
@image-pipeline "Rebuild the image registry"
```

## Commands

### Face Swap Batch
```bash
cd /Users/shane/Documents/AestheticcTools/project_files/tiktok-ig
.venv/bin/python3 generators/batch_face_swap.py \
    /path/to/input \
    /path/to/output \
    5  # delay between API calls
```

### Screen Swap Batch
```bash
.venv/bin/python3 generators/batch_screen_swap.py \
    /path/to/input \
    /path/to/output \
    5
```

### Registry Status
```bash
.venv/bin/python3 make_pack.py --status
```

## Cost
- Face swap: ~$0.14 per image (Gemini)
- Screen swap: ~$0.14 per image (Gemini)
- Use 5-10 second delays to avoid rate limits
