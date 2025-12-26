from __future__ import annotations

import argparse
import base64
from pathlib import Path

import requests


def _post_json(url: str, payload: dict, timeout: float) -> requests.Response:
    return requests.post(url, json=payload, timeout=timeout)


def _image_as_data_url(path: Path) -> str:
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    # Assuming PNG captures.
    return f"data:image/png;base64,{encoded}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:1234/v1")
    parser.add_argument("--model", default="gemma-3-4b-it-qat")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--image", type=str, default="")
    args = parser.parse_args()

    url = f"{args.base_url.rstrip('/')}/chat/completions"

    print("== text only ==")
    payload_text = {
        "model": args.model,
        "messages": [{"role": "user", "content": "ping: JSONで {\"ok\": true} だけ返して"}],
        "temperature": 0.0,
        "max_tokens": 50,
        "stream": False,
    }
    r = _post_json(url, payload_text, args.timeout)
    print("HTTP", r.status_code)
    print(r.text[:1200])

    if args.image:
        print("\n== with image_url ==")
        img = Path(args.image)
        data_url = _image_as_data_url(img)
        payload_img = {
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "この画像の内容を1文で説明して"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0.2,
            "max_tokens": 120,
            "stream": False,
        }
        r2 = _post_json(url, payload_img, args.timeout)
        print("HTTP", r2.status_code)
        print(r2.text[:1200])


if __name__ == "__main__":
    main()
