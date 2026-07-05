import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def natural_key(path):
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def sorted_images(inputs, sort_mode):
    files = []
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            files.extend(child for child in path.iterdir() if child.is_file() and child.suffix.lower() in IMAGE_EXTS)
        elif path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            files.append(path)
    if sort_mode == "name":
        return sorted(files, key=natural_key)
    if sort_mode == "mtime":
        return sorted(files, key=lambda p: (p.stat().st_mtime, natural_key(p)))
    if sort_mode == "ctime":
        return sorted(files, key=lambda p: (p.stat().st_ctime, natural_key(p)))
    raise ValueError(f"Unsupported sort mode: {sort_mode}")


def apply_range(files, start=None, end=None, limit=None):
    if start or end:
        first = max((start or 1) - 1, 0)
        last = end if end else None
        files = files[first:last]
    if limit:
        files = files[:limit]
    return files


def run_command(cmd, env):
    return subprocess.run(cmd, env=env, text=True, capture_output=True, encoding="utf-8", errors="replace")


def run_ocr(image, out_dir, model, timeout, interval, env):
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "paddleocr_async.py"),
        str(image),
        "--out",
        str(out_dir),
        "--model",
        model,
        "--timeout",
        str(timeout),
        "--interval",
        str(interval),
        "--no-proxy-api",
        "--no-proxy-download",
    ]
    return run_command(cmd, env)


def main():
    parser = argparse.ArgumentParser(description="Batch prepare images, run PaddleOCR async API, and produce per-image plus combined text outputs.")
    parser.add_argument("inputs", nargs="+", help="Image files or directories")
    parser.add_argument("--out-root", required=True, help="Output root directory")
    parser.add_argument("--sort", choices=["name", "mtime", "ctime"], default="name")
    parser.add_argument("--start", type=int, help="1-based start index after sorting")
    parser.add_argument("--end", type=int, help="1-based end index after sorting")
    parser.add_argument("--limit", type=int, help="Maximum images after sorting/range")
    parser.add_argument("--crop", help="Optional crop box left,top,right,bottom")
    parser.add_argument("--max-width", type=int, default=1800)
    parser.add_argument("--model", default="PaddleOCR-VL-1.6")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--baseline-jpeg", action="store_true", help="Use conservative baseline JPEG preprocessing")
    parser.add_argument("--dry-run", action="store_true", help="Write ordering preview only; do not run OCR")
    args = parser.parse_args()

    skill_dir = Path(__file__).resolve().parents[1]
    prepare_script = skill_dir / "scripts" / "prepare_photo_inputs.py"
    ocr_script = skill_dir / "scripts" / "paddleocr_async.py"

    out_root = Path(args.out_root)
    prepared_dir = out_root / "prepared"
    ocr_dir = out_root / "ocr"
    per_image_dir = out_root / "per-image-text"
    combined_dir = out_root / "combined"
    reports_dir = out_root / "reports"
    for directory in [prepared_dir, ocr_dir, per_image_dir, combined_dir, reports_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    selected = apply_range(sorted_images(args.inputs, args.sort), args.start, args.end, args.limit)
    ordering = [
        {
            "order_index": index,
            "source": str(path),
            "name": path.name,
            "bytes": path.stat().st_size,
            "mtime": path.stat().st_mtime,
            "ctime": path.stat().st_ctime,
        }
        for index, path in enumerate(selected, start=1)
    ]
    (reports_dir / "ordering_preview.json").write_text(json.dumps(ordering, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.dry_run:
        print(json.dumps({"dry_run": True, "count": len(ordering), "preview": str(reports_dir / "ordering_preview.json")}, ensure_ascii=False, indent=2))
        return

    prepare_cmd = [
        sys.executable,
        str(prepare_script),
        *[item["source"] for item in ordering],
        "--out",
        str(prepared_dir),
        "--sort",
        "input",
        "--max-width",
        str(args.max_width),
        "--format",
        "jpg",
    ]
    if args.crop:
        prepare_cmd.extend(["--crop", args.crop])
    if args.baseline_jpeg:
        prepare_cmd.append("--baseline-jpeg")
    prepare_result = run_command(prepare_cmd, os.environ.copy())
    (reports_dir / "prepare_stdout.txt").write_text(prepare_result.stdout, encoding="utf-8")
    (reports_dir / "prepare_stderr.txt").write_text(prepare_result.stderr, encoding="utf-8")
    if prepare_result.returncode != 0:
        raise SystemExit(f"prepare failed: {reports_dir / 'prepare_stderr.txt'}")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("HTTP_PROXY", "http://127.0.0.1:29290")
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:29290")

    rows = []
    failures = []
    combined = []
    prepared_images = sorted(prepared_dir.glob("*.jpg"), key=natural_key)
    for item, image in zip(ordering, prepared_images):
        out_dir = ocr_dir / f"{item['order_index']:03d}-{Path(item['source']).stem}"
        started = time.time()
        print(f"OCR {item['order_index']:03d}: {Path(item['source']).name}")
        completed = run_ocr(image, out_dir, args.model, args.timeout, args.interval, env)
        elapsed = round(time.time() - started, 2)
        summary_path = out_dir / "summary.json"
        clean_path = out_dir / "clean_text.md"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        clean_text = clean_path.read_text(encoding="utf-8").strip() if clean_path.exists() else ""
        fallback_model = None
        fallback_reason = None
        if completed.returncode == 0 and not clean_text and args.model != "PP-OCRv5":
            fallback_dir = out_dir.parent / f"{out_dir.name}-pp-ocrv5"
            fallback_completed = run_ocr(image, fallback_dir, "PP-OCRv5", args.timeout, args.interval, env)
            fallback_summary_path = fallback_dir / "summary.json"
            fallback_clean_path = fallback_dir / "clean_text.md"
            fallback_text = fallback_clean_path.read_text(encoding="utf-8").strip() if fallback_clean_path.exists() else ""
            if fallback_completed.returncode == 0 and fallback_text:
                completed = fallback_completed
                out_dir = fallback_dir
                summary_path = fallback_summary_path
                clean_path = fallback_clean_path
                summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
                clean_text = fallback_text
                fallback_model = "PP-OCRv5"
                fallback_reason = "primary model returned no clean text"

        per_image_path = per_image_dir / f"{item['order_index']:03d}-{Path(item['source']).stem}.md"
        per_image_path.write_text(f"# {item['order_index']:03d} {Path(item['source']).name}\n\n{clean_text}\n", encoding="utf-8")
        combined.append(per_image_path.read_text(encoding="utf-8"))

        row = {
            **item,
            "prepared": str(image),
            "ocr_dir": str(out_dir),
            "per_image_text": str(per_image_path),
            "returncode": completed.returncode,
            "elapsed_seconds": elapsed,
            "fallback_model": fallback_model,
            "fallback_reason": fallback_reason,
            "pages": summary.get("pages"),
            "blocks": summary.get("blocks"),
            "chars": len(clean_text),
            "preview": clean_text.replace("\n", " ")[:160],
            "stdout": completed.stdout[-3000:],
            "stderr": completed.stderr[-3000:],
        }
        rows.append(row)
        if completed.returncode != 0:
            failures.append(row)

    (combined_dir / "all_text.md").write_text("\n---\n\n".join(combined).strip() + "\n", encoding="utf-8")
    (reports_dir / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (reports_dir / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"total": len(rows), "ok": sum(row["returncode"] == 0 for row in rows), "failed": len(failures), "out_root": str(out_root)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
