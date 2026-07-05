import argparse
import json
import re
from pathlib import Path

from PIL import Image


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def parse_crop(value):
    if not value:
        return None
    parts = [int(p.strip()) for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--crop must be left,top,right,bottom")
    return tuple(parts)


def natural_key(path):
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def sort_images(images, mode):
    if mode == "input":
        return list(images)
    if mode == "name":
        return sorted(images, key=natural_key)
    if mode == "mtime":
        return sorted(images, key=lambda p: (p.stat().st_mtime, natural_key(p)))
    if mode == "ctime":
        return sorted(images, key=lambda p: (p.stat().st_ctime, natural_key(p)))
    raise ValueError(f"Unsupported sort mode: {mode}")


def iter_images(paths, sort_mode="name"):
    images = []
    for path in paths:
        p = Path(path)
        if p.is_dir():
            for child in p.iterdir():
                if child.suffix.lower() in IMAGE_EXTS:
                    images.append(child)
        elif p.suffix.lower() in IMAGE_EXTS:
            images.append(p)
    yield from sort_images(images, sort_mode)


def resize_keep_aspect(image, max_width):
    if not max_width or image.width <= max_width:
        return image
    height = round(image.height * (max_width / image.width))
    return image.resize((max_width, height), Image.Resampling.LANCZOS)


def flatten_rgb(image):
    if image.mode in ("RGBA", "LA") or ("transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="Crop and resize photos before OCR.")
    parser.add_argument("inputs", nargs="+", help="Image files or directories")
    parser.add_argument("--out", required=True, help="Output directory for prepared images")
    parser.add_argument("--crop", help="Optional crop box: left,top,right,bottom")
    parser.add_argument("--max-width", type=int, default=1800, help="Resize images wider than this")
    parser.add_argument("--format", choices=["jpg", "png"], default="jpg")
    parser.add_argument("--sort", choices=["input", "name", "mtime", "ctime"], default="name", help="Input ordering")
    parser.add_argument("--start", type=int, help="1-based start index after sorting")
    parser.add_argument("--end", type=int, help="1-based end index after sorting")
    parser.add_argument("--limit", type=int, help="Maximum number of images after start/end filtering")
    parser.add_argument("--baseline-jpeg", action="store_true", help="Save conservative baseline RGB JPEGs")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    crop = parse_crop(args.crop)
    manifest = []

    sources = list(iter_images(args.inputs, args.sort))
    if args.start or args.end:
        start = max((args.start or 1) - 1, 0)
        end = args.end if args.end else None
        sources = sources[start:end]
    if args.limit:
        sources = sources[: args.limit]

    for index, src in enumerate(sources, start=1):
        with Image.open(src) as im:
            image = flatten_rgb(im)
            original_size = [image.width, image.height]
            if crop:
                image = image.crop(crop)
            crop_size = [image.width, image.height]
            image = resize_keep_aspect(image, args.max_width)

            suffix = ".jpg" if args.format == "jpg" else ".png"
            dst = out_dir / f"{index:03d}-{src.stem}{suffix}"
            if args.format == "jpg" and args.baseline_jpeg:
                clean = Image.new("RGB", image.size, (255, 255, 255))
                clean.paste(image)
                image = clean
                save_kwargs = {"format": "JPEG", "quality": 92, "progressive": False, "optimize": False, "subsampling": 0}
            else:
                save_kwargs = {"quality": 92, "optimize": True} if args.format == "jpg" else {}
            image.save(dst, **save_kwargs)

        manifest.append(
            {
                "source": str(src),
                "prepared": str(dst),
                "order_index": index,
                "sort_mode": args.sort,
                "original_size": original_size,
                "crop_box": list(crop) if crop else None,
                "crop_size": crop_size,
                "prepared_size": [image.width, image.height],
            }
        )

    (out_dir / "prepared_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Prepared {len(manifest)} image(s): {out_dir}")


if __name__ == "__main__":
    main()
