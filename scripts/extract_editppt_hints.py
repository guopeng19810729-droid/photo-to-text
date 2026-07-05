import argparse
import json
from pathlib import Path


def load_page_hints(run_dir):
    pages_dir = Path(run_dir) / "pages"
    for page_dir in sorted(pages_dir.glob("page_*")):
        hints_path = page_dir / "text_hints.json"
        if not hints_path.exists():
            continue
        data = json.loads(hints_path.read_text(encoding="utf-8"))
        yield page_dir.name, hints_path, data


def line_text(line):
    return str(line.get("text") or "").strip()


def main():
    parser = argparse.ArgumentParser(description="Extract editppt text_hints.json files to Markdown and JSONL.")
    parser.add_argument("--run-dir", required=True, help="editppt run directory")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--min-confidence", type=float, default=0.0)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    markdown = []
    jsonl_rows = []
    for page_name, hints_path, data in load_page_hints(args.run_dir):
        markdown.append(f"## {page_name}\n")
        for line in data.get("lines", []):
            text = line_text(line)
            if not text:
                continue
            conf = line.get("confidence")
            if conf is not None and conf < args.min_confidence:
                continue
            markdown.append(text.replace("\n", " ") + "\n")
            jsonl_rows.append(
                {
                    "page": page_name,
                    "id": line.get("id"),
                    "text": text,
                    "confidence": conf,
                    "box_px": line.get("box_px"),
                    "source": str(hints_path),
                }
            )
        markdown.append("")

    (out_dir / "ocr_text.md").write_text("\n".join(markdown).strip() + "\n", encoding="utf-8")
    with (out_dir / "ocr_blocks.jsonl").open("w", encoding="utf-8") as f:
        for row in jsonl_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "run_dir": str(Path(args.run_dir)),
        "pages": len({row["page"] for row in jsonl_rows}),
        "text_blocks": len(jsonl_rows),
        "outputs": ["ocr_text.md", "ocr_blocks.jsonl"],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
