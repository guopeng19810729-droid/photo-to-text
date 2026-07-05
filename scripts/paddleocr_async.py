import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests


JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"


def read_token(explicit_token=None):
    if explicit_token:
        return explicit_token.strip()
    env_token = os.environ.get("PADDLE_OCR_TOKEN")
    if env_token:
        return env_token.strip()

    config_path = Path.home() / ".editppt" / "config.yaml"
    if config_path.exists():
        for line in config_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("PADDLE_OCR_TOKEN:"):
                return line.split(":", 1)[1].strip().strip("\"'")

    raise RuntimeError("PaddleOCR token not found. Set PADDLE_OCR_TOKEN or configure ~/.editppt/config.yaml.")


def request_json(response, context):
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{context} returned non-JSON HTTP {response.status_code}: {response.text[:500]}") from exc
    if response.status_code != 200:
        raise RuntimeError(f"{context} failed HTTP {response.status_code}: {json.dumps(payload, ensure_ascii=False)}")
    code = payload.get("code")
    if code not in (None, 0):
        raise RuntimeError(f"{context} failed code={code}: {payload.get('msg') or payload.get('message')}")
    return payload


def request_with_retries(method, url, *, retries=5, sleep_seconds=3, use_env_proxy=True, **kwargs):
    last_exc = None
    session = requests.Session()
    session.trust_env = use_env_proxy
    for attempt in range(1, retries + 1):
        try:
            return session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == retries:
                break
            print(f"{method.upper()} retry {attempt}/{retries} after {exc.__class__.__name__}")
            time.sleep(sleep_seconds)
    raise RuntimeError(f"{method.upper()} {url} failed after {retries} attempt(s): {last_exc}")


def submit_job(path_or_url, token, model, optional_payload, page_ranges=None, batch_id=None, use_env_proxy=True):
    headers = {"Authorization": f"bearer {token}"}
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        headers["Content-Type"] = "application/json"
        payload = {
            "fileUrl": path_or_url,
            "model": model,
            "optionalPayload": optional_payload,
        }
        if page_ranges:
            payload["pageRanges"] = page_ranges
        if batch_id:
            payload["batchId"] = batch_id
        response = request_with_retries("post", JOB_URL, json=payload, headers=headers, timeout=60, use_env_proxy=use_env_proxy)
    else:
        path = Path(path_or_url)
        if not path.exists():
            raise FileNotFoundError(path)
        data = {
            "model": model,
            "optionalPayload": json.dumps(optional_payload, ensure_ascii=False),
        }
        if page_ranges:
            data["pageRanges"] = page_ranges
        if batch_id:
            data["batchId"] = batch_id
        with path.open("rb") as f:
            response = request_with_retries("post", JOB_URL, headers=headers, data=data, files={"file": f}, timeout=120, use_env_proxy=use_env_proxy)

    payload = request_json(response, "submit job")
    job_id = payload.get("data", {}).get("jobId")
    if not job_id:
        raise RuntimeError(f"submit job response missing jobId: {json.dumps(payload, ensure_ascii=False)}")
    return job_id, payload


def poll_job(job_id, token, timeout_seconds, interval_seconds, status_log_path, use_env_proxy=True):
    headers = {"Authorization": f"bearer {token}", "Content-Type": "application/json"}
    started = time.monotonic()
    last_payload = None
    with status_log_path.open("w", encoding="utf-8") as log:
        while True:
            response = request_with_retries("get", f"{JOB_URL}/{job_id}", headers=headers, timeout=60, use_env_proxy=use_env_proxy)
            payload = request_json(response, "poll job")
            last_payload = payload
            data = payload.get("data", {})
            state = data.get("state")
            log.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **payload}, ensure_ascii=False) + "\n")
            log.flush()

            progress = data.get("extractProgress") or {}
            if state in ("pending", "running"):
                total = progress.get("totalPages")
                done = progress.get("extractedPages")
                if total is not None or done is not None:
                    print(f"state={state}, pages={done}/{total}")
                else:
                    print(f"state={state}")
            elif state == "done":
                return payload
            elif state == "failed":
                raise RuntimeError(f"job failed: {data.get('errorMsg') or json.dumps(payload, ensure_ascii=False)}")
            else:
                raise RuntimeError(f"unknown job state={state}: {json.dumps(payload, ensure_ascii=False)}")

            if time.monotonic() - started > timeout_seconds:
                raise TimeoutError(f"job {job_id} timed out after {timeout_seconds}s; last state={state}")
            time.sleep(interval_seconds)


def get_with_retries(url, timeout=120, retries=5, sleep_seconds=3, use_env_proxy=True):
    last_exc = None
    session = requests.Session()
    session.trust_env = use_env_proxy
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == retries:
                break
            print(f"download retry {attempt}/{retries} after {exc.__class__.__name__}")
            time.sleep(sleep_seconds)
    raise RuntimeError(f"download failed after {retries} attempt(s): {last_exc}")


def download_text(url, path, use_env_proxy=True):
    response = get_with_retries(url, use_env_proxy=use_env_proxy)
    text = response.content.decode("utf-8")
    path.write_text(text, encoding="utf-8")
    return text


def download_binary(url, path, use_env_proxy=True):
    response = get_with_retries(url, use_env_proxy=use_env_proxy)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)


def parse_results(jsonl_text, out_dir, download_images=False, use_env_proxy=True):
    combined_markdown = []
    clean_lines = []
    block_rows = []
    page_num = 0

    for line_num, line in enumerate(jsonl_text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        result = record.get("result", {})
        layout_results = result.get("layoutParsingResults") or []
        ocr_results = result.get("ocrResults") or []

        for item in layout_results:
            markdown = item.get("markdown") or {}
            md_text = markdown.get("text") or ""
            md_path = out_dir / f"doc_{page_num:03d}.md"
            md_path.write_text(md_text, encoding="utf-8")
            combined_markdown.append(f"<!-- page {page_num:03d}, line {line_num} -->\n{md_text}\n")

            pruned = item.get("prunedResult") or {}
            parsing_blocks = pruned.get("parsing_res_list") or pruned.get("layout_parsing_res_list") or []
            for block_index, block in enumerate(parsing_blocks):
                block_label = block.get("block_label") or block.get("label")
                block_content = block.get("block_content") or block.get("text") or block.get("content")
                if block_content and block_label not in {"image", "seal", "footer_image", "header_image"}:
                    clean_lines.append(str(block_content).strip())
                block_rows.append(
                    {
                        "page": page_num,
                        "block_index": block_index,
                        "block_label": block_label,
                        "block_content": block_content,
                        "block_bbox": block.get("block_bbox") or block.get("bbox"),
                        "raw": block,
                    }
                )

            if download_images:
                for rel_path, url in (markdown.get("images") or {}).items():
                    download_binary(url, out_dir / rel_path, use_env_proxy=use_env_proxy)
                for image_name, url in (item.get("outputImages") or {}).items():
                    download_binary(url, out_dir / "images" / f"{image_name}_{page_num:03d}.jpg", use_env_proxy=use_env_proxy)
            page_num += 1

        for item in ocr_results:
            pruned = item.get("prunedResult") or {}
            rec_texts = pruned.get("rec_texts") or item.get("rec_texts")
            rec_scores = pruned.get("rec_scores") or item.get("rec_scores") or []
            rec_boxes = pruned.get("rec_boxes") or item.get("rec_boxes") or []
            if rec_texts:
                page_texts = [str(text).strip() for text in rec_texts if str(text).strip()]
                combined_markdown.append(f"<!-- page {page_num:03d}, line {line_num} -->\n" + "\n".join(page_texts) + "\n")
                clean_lines.extend(page_texts)
                for block_index, text in enumerate(rec_texts):
                    block_rows.append(
                        {
                            "page": page_num,
                            "block_index": block_index,
                            "block_label": "text",
                            "block_content": text,
                            "confidence": rec_scores[block_index] if block_index < len(rec_scores) else None,
                            "block_bbox": rec_boxes[block_index] if block_index < len(rec_boxes) else None,
                            "raw": {
                                "text": text,
                                "score": rec_scores[block_index] if block_index < len(rec_scores) else None,
                                "box": rec_boxes[block_index] if block_index < len(rec_boxes) else None,
                            },
                        }
                    )
            else:
                text = item.get("recText") or item.get("text") or json.dumps(item, ensure_ascii=False)
                combined_markdown.append(f"<!-- page {page_num:03d}, line {line_num} -->\n{text}\n")
                clean_lines.append(str(text).strip())
                block_rows.append({"page": page_num, "block_index": 0, "block_content": text, "raw": item})
            page_num += 1

    (out_dir / "ocr_text.md").write_text("\n".join(combined_markdown).strip() + "\n", encoding="utf-8")
    (out_dir / "clean_text.md").write_text("\n\n".join(line for line in clean_lines if line).strip() + "\n", encoding="utf-8")
    with (out_dir / "ocr_blocks.jsonl").open("w", encoding="utf-8") as f:
        for row in block_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"pages": page_num, "blocks": len(block_rows)}


def main():
    parser = argparse.ArgumentParser(description="Run Baidu AI Studio PaddleOCR async API and save reusable text artifacts.")
    parser.add_argument("input", help="Local file path or file URL")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--model", default="PaddleOCR-VL-1.6", help="OCR model name")
    parser.add_argument("--timeout", type=int, default=900, help="Total polling timeout in seconds")
    parser.add_argument("--interval", type=int, default=5, help="Polling interval in seconds")
    parser.add_argument("--token", help="Token override; prefer env/config instead")
    parser.add_argument("--page-ranges", help="Page ranges for PDF, such as 2,4-6")
    parser.add_argument("--batch-id", help="Optional batch id")
    parser.add_argument("--optional-payload", help="JSON object string for optionalPayload")
    parser.add_argument("--download-images", action="store_true", help="Download markdown/output images")
    parser.add_argument("--done-response", help="Skip submit/poll and download from an existing done_response.json")
    parser.add_argument("--job-id", help="Skip submit and continue polling an existing job id")
    parser.add_argument("--no-proxy-api", action="store_true", help="Ignore environment proxy for submit/poll API calls")
    parser.add_argument("--no-proxy-download", action="store_true", help="Ignore environment proxy while downloading result URLs")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    optional_payload = {
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }
    if args.optional_payload:
        optional_payload.update(json.loads(args.optional_payload))

    if args.done_response:
        done_payload = json.loads(Path(args.done_response).read_text(encoding="utf-8"))
        job_id = done_payload.get("data", {}).get("jobId") or "from-done-response"
        print(f"using existing done response for job: {job_id}")
    else:
        token = read_token(args.token)
        use_api_proxy = not args.no_proxy_api
        if args.job_id:
            job_id = args.job_id
            print(f"using existing job id: {job_id}")
        else:
            job_id, submit_payload = submit_job(args.input, token, args.model, optional_payload, args.page_ranges, args.batch_id, use_env_proxy=use_api_proxy)
            (out_dir / "submit_response.json").write_text(json.dumps(submit_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"job submitted: {job_id}")
        done_payload = poll_job(job_id, token, args.timeout, args.interval, out_dir / "status_log.jsonl", use_env_proxy=use_api_proxy)
        (out_dir / "done_response.json").write_text(json.dumps(done_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result_url = done_payload.get("data", {}).get("resultUrl") or {}
    json_url = result_url.get("jsonUrl")
    markdown_url = result_url.get("markdownUrl")
    if not json_url:
        raise RuntimeError("done response missing resultUrl.jsonUrl")

    use_env_proxy = not args.no_proxy_download
    jsonl_text = download_text(json_url, out_dir / "result.jsonl", use_env_proxy=use_env_proxy)
    if markdown_url:
        download_text(markdown_url, out_dir / "result_markdown_download.md", use_env_proxy=use_env_proxy)

    parsed = parse_results(jsonl_text, out_dir, download_images=args.download_images, use_env_proxy=use_env_proxy)
    summary = {
        "input": args.input,
        "model": args.model,
        "job_id": job_id,
        "json_url_present": bool(json_url),
        "markdown_url_present": bool(markdown_url),
        "pages": parsed["pages"],
        "blocks": parsed["blocks"],
        "outputs": [
            "result.jsonl",
            "ocr_text.md",
            "clean_text.md",
            "ocr_blocks.jsonl",
            "submit_response.json",
            "done_response.json",
            "status_log.jsonl",
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
