# PaddleOCR API Notes

Use the asynchronous API as the default path for photo/document OCR.

## Asynchronous API

- Submit: `POST https://paddleocr.aistudio-app.com/api/v2/ocr/jobs`
- Poll: `GET https://paddleocr.aistudio-app.com/api/v2/ocr/jobs/{jobId}`
- Authorization header: `Bearer {access_token}`
- Local file upload limit: 50 MB
- File URL limit: 200 MB
- PDF request limit: up to 1000 pages

Successful jobs return `data.resultUrl.jsonUrl` and often `data.resultUrl.markdownUrl`.

States:

- `pending`: queued
- `running`: parsing
- `done`: completed
- `failed`: no partial success; inspect `data.errorMsg`

## Models

Use `PaddleOCR-VL-1.6` when available for robust Chinese PPT/photo parsing.
Use `PaddleOCR-VL-1.5` if 1.6 is unavailable.
Use `PP-StructureV3` for structure-heavy documents.
Use `PP-OCRv5` for simple OCR-only extraction.

## Error Codes

- `401`: token invalid
- `500`: system error
- `10001`: empty file
- `10002`: file URL unrecognized
- `10003`: file size exceeds limit
- `10004`: file format not supported
- `10005`: file content cannot be parsed
- `10006`: page count exceeds limit
- `10007`: model parameter error
- `10008`: request parameter error
- `10009`: too many tasks for same batchId
- `10010`: job submission queue full
- `11001`: jobId does not exist
- `11002`: job expired
- `11003`: job parsing failed
- `12001`: daily page limit reached
- `12002`: request frequency too high
