---
name: photo-to-text
description: Prepare photographed slides, classroom screen photos, document photos, screenshots, or scanned images for reliable OCR text extraction. Use when Codex needs to turn images into clean reusable text before making PPT, Markdown notes, summaries, study materials, or editable documents; especially for Chinese PPT photos where PaddleOCR/Baidu OCR should be preferred over weak offline OCR. Trigger aliases include "ocr", "拍照转文字", "图片转文字", "照片OCR", "百度OCR", and "识别图片文字".
---

# Photo To Text

Common aliases: ocr, 拍照转文字, 图片转文字, 照片OCR, 百度OCR, 识别图片文字.

## Purpose

Turn imperfect photos or screenshots into clean text artifacts that downstream tasks can trust. Treat this as a required preflight step before recreating a PPT, summarizing photographed notes, or extracting classroom/document content from images.

## Workflow

Before running a non-test job, confirm these with the user:

1. Output location. Default to a `photo-to-text-output` folder beside the source folder unless the user chooses another location.
2. Ordering rule. Default to natural filename order. If filenames are not trustworthy, ask whether to sort by modified time, created time, or rename first.
3. Whether to preview ordering first. For large or messy folders, run a dry-run and show the first/last items before OCR.
4. Whether the user wants both per-image text and a combined document. Default to both.

Then:

1. Inspect the input images and identify the real content area.
2. Crop away irrelevant surroundings such as classroom walls, monitor bezels, desks, black borders, app toolbars, and camera UI.
3. Re-encode suspicious images to standard RGB JPEG when the service reports unsupported format even though the file opens locally.
4. Resize large photos before OCR. Prefer 1600-2200 px width for PPT/screen photos unless the text is tiny.
5. Run OCR with the best available backend.
6. Save both raw OCR and cleaned text. Never rely only on memory or chat text.
7. Flag uncertain text instead of silently guessing when OCR is ambiguous.

## OCR Backend Choice

Prefer PaddleOCR/Baidu OCR for Chinese photos, photographed PPT, multi-block layouts, and screenshots with small text. It is usually much better than local lightweight OCR for these cases.

Use offline/local OCR only when privacy, no-network operation, or quick rough triage matters more than accuracy. For photographed PPT, treat local OCR as a weak fallback.

Do not place OCR tokens in this skill. Use existing local configuration, such as the `editppt` PaddleOCR token config, environment variables, or another local secret store.

## Standard Artifacts

For each image batch, create a durable work folder containing:

- `prepared/`: images after crop/resize/re-encode
- `ocr/`: one OCR run folder per image, including raw JSONL and block JSONL
- `per-image-text/`: one cleaned Markdown file per image
- `combined/all_text.md`: all cleaned text in the confirmed order
- `reports/ordering_preview.json`: exact source order used
- `reports/summary.json`: per-image status, timing, block count, preview
- `reports/failures.json`: failed images and diagnostic output

Use stable filenames so downstream PPT or Markdown tasks can cite them.

## Recommended Current Toolchain

Prefer the Baidu AI Studio PaddleOCR asynchronous API for serious Chinese photo/PPT OCR. It submits a parsing job, polls until completion, then downloads JSONL/Markdown results. This is more reliable for long-running document parsing than short OCR helper timeouts.

1. Use `scripts/prepare_photo_inputs.py` to crop/resize the input photos.
2. Use `scripts/paddleocr_async.py` to submit prepared images to PaddleOCR and save Markdown, raw JSONL, block JSONL, and a run summary.
3. For normal batch jobs, prefer `scripts/batch_photo_to_text.py`; it wraps preparation, OCR, ordering reports, per-image text, combined text, and failure reports.
4. Use the saved Markdown/JSON artifacts as the source for downstream PPT, notes, or document tasks.

The main text outputs are:

- `clean_text.md`: plain OCR text blocks, easiest for PPT/notes
- `ocr_text.md`: Markdown returned by the OCR service, may include image tags
- `ocr_blocks.jsonl`: structured blocks with labels and bounding boxes
- `result.jsonl`: raw service result

For a single prepared image:

```powershell
python '<skill-dir>\scripts\prepare_photo_inputs.py' '<source-image>' --out '<prepared-dir>' --crop left,top,right,bottom --max-width 1600
python '<skill-dir>\scripts\paddleocr_async.py' '<prepared-image>' --out '<ocr-out-dir>' --model PaddleOCR-VL-1.6 --timeout 900
```

For a batch:

```powershell
python '<skill-dir>\scripts\batch_photo_to_text.py' '<source-folder>' --out-root '<output-folder>' --sort name --baseline-jpeg
```

Use `--dry-run` first when ordering may be ambiguous:

```powershell
python '<skill-dir>\scripts\batch_photo_to_text.py' '<source-folder>' --out-root '<output-folder>' --sort mtime --dry-run
```

The script reads the token from `PADDLE_OCR_TOKEN` or local config such as `%USERPROFILE%\.editppt\config.yaml`. Never print or commit the token.

If a job was submitted but polling or result download failed because of network/SSL interruption, continue instead of resubmitting:

```powershell
python '<skill-dir>\scripts\paddleocr_async.py' '<prepared-image>' --out '<ocr-out-dir>' --job-id '<job-id>' --timeout 900 --no-proxy-download
python '<skill-dir>\scripts\paddleocr_async.py' resume --out '<ocr-out-dir>' --done-response '<done_response.json>' --no-proxy-download
```

If `image-to-editable-ppt` / `editppt` is already needed for editable PPT reconstruction, its text hints can still be extracted:

```powershell
$env:PATH='C:\Users\ROG\.local\bin;' + $env:PATH
editppt prepare '<prepared-image>' --job-dir '<run-dir>' --no-text-hints
editppt run hints '<run-dir>'
python '<skill-dir>\scripts\extract_editppt_hints.py' --run-dir '<run-dir>' --out '<out-dir>'
```

In Windows PowerShell, use `-LiteralPath` for non-ASCII paths when inspecting files. For complex batch work, prefer a small `.py` or `.ps1` script rather than fragile one-line shell logic.

## PaddleOCR API Notes

Use the asynchronous endpoint when possible:

- submit job: `POST https://paddleocr.aistudio-app.com/api/v2/ocr/jobs`
- poll job: `GET https://paddleocr.aistudio-app.com/api/v2/ocr/jobs/{jobId}`
- successful state: `done`
- waiting states: `pending`, `running`
- failed state: `failed`

Do not treat `running` as failure. Poll long enough for large or busy jobs. For classroom PPT photos, start with 900 seconds total timeout and 5 seconds poll interval.

Treat transient `SSLError` / `UNEXPECTED_EOF_WHILE_READING` as a network issue. Retry submit, poll, or download before changing OCR parameters. When BOS result download fails through a local proxy, retry with `--no-proxy-download`.

For Baidu OCR multipart uploads, local proxies can corrupt or interrupt requests and surface as misleading `10004 unsupported file format`. Prefer `--no-proxy-api --no-proxy-download` when direct access works.

Record `traceId`, `code`, `msg`, `jobId`, `state`, `errorMsg`, and `extractProgress` in output artifacts for debugging.

Save and read downloaded text as UTF-8. If Windows PowerShell displays Chinese as mojibake, verify the artifact in a UTF-8 editor or with Python before assuming OCR failed.

Common failure meanings:

- `401`: token invalid
- `10003`: file too large
- `10004`: unsupported file format
- `10007`: wrong model name
- `10008`: bad optional payload
- `10010`: job queue full, retry later
- `11003`: job parsing failed, inspect `data.errorMsg`
- `12001`: daily page quota reached
- `12002`: request frequency too high, retry later

If `10004` appears for a normal-looking JPG/PNG that opens locally, copy it to an ASCII path and re-save it as RGB JPEG with Pillow, then resubmit.

If `PaddleOCR-VL-1.6` succeeds but `clean_text.md` is empty while `result.jsonl` shows the page was classified as one `image` block, rerun that image with `PP-OCRv5`. This is common for timeline/infographic screenshots where document-layout parsing treats the whole page as a graphic.

## Cleaning Rules

Preserve original meaning and order. Keep headings, numbered lists, and slide/page boundaries.

Fix obvious OCR artifacts such as stray spaces, duplicated line breaks, and split list markers. Do not rewrite the teacher's wording unless the user asks for a rewritten version.

When OCR splits one phrase across multiple boxes, merge it in cleaned text and keep the raw block records for traceability.

When the output will feed PPT reconstruction, keep one section per source image and preserve slide-like hierarchy: title, subtitle, body bullets, bottom notes.

## When To Stop And Ask

Ask the user before sending sensitive private images to cloud OCR if the source appears to contain personal identifiers, financial data, medical records, passwords, private chat logs, or confidential business data.

If OCR quality is poor after crop/resize, show a short diagnosis and propose one of: tighter crop, higher-resolution source, manual correction pass, or a different OCR backend.
