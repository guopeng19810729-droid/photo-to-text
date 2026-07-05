# Photo To Text Workflow Notes

## Recommended batch folder layout

```text
photo-ocr-work/
  prepared/
  ocr-runs/
  extracted/
  notes.md
```

## Practical image preparation defaults

- PPT or monitor photo: crop content rectangle first, then resize to 1600-2200 px width.
- Full document page: crop page boundary first, preserve aspect ratio, use 1800-2400 px width.
- Blurry photo: avoid over-sharpening in the first pass; try a tighter crop and a slightly larger width.
- Screenshots: usually do not need resizing unless very large.

## Quality checklist

- Did OCR capture the title?
- Did it preserve list numbering?
- Did it merge split Chinese phrases correctly?
- Are slide/page boundaries clear?
- Are uncertain words marked for review?
