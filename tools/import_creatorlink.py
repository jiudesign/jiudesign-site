#!/usr/bin/env python3
"""
Creatorlink 사이트 → 새 사이트 이전 도구

    # 1) portfolio.yaml 에 등록된 작업물의 원본 이미지를 content/images/ 로 내려받기
    python tools/import_creatorlink.py --download

    # 2) Creatorlink 각 분류 페이지를 훑어 portfolio.yaml 에 아직 없는 작업물 찾기
    #    → content/portfolio.new.yaml 에 초안 저장 (검토 후 portfolio.yaml 로 옮겨 붙이기)
    python tools/import_creatorlink.py --scan

    # 둘 다
    python tools/import_creatorlink.py --scan --download

옵션
    --site URL     기본값 https://jiudesigner.creatorlink.net
    --force        이미 내려받은 이미지도 다시 받기

원본(=s0)은 업로드한 그대로의 해상도입니다. 내려받은 파일은 build.py 가 WebP로 변환합니다.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
PORTFOLIO = CONTENT / "portfolio.yaml"
IMAGES = CONTENT / "images"
UA = "Mozilla/5.0 (JIUDESIGN site migration)"
IMG_HOST = "googleusercontent.com"
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


def fetch(url: str, timeout: int = 60) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers.get_content_type()


def source_key(url: str) -> str:
    """같은 이미지인지 비교할 때 크기 파라미터(=s0, =w640 …)는 무시"""
    return url.split("=")[0].split("?")[0]


# ─────────────────────────────────────────────
# --download
# ─────────────────────────────────────────────
def download(force: bool) -> None:
    items = yaml.safe_load(PORTFOLIO.read_text(encoding="utf-8")) or []
    text = PORTFOLIO.read_text(encoding="utf-8")
    changed = False
    ok = skip = fail = 0
    for it in items:
        src, rel = it.get("source"), it.get("image")
        if not src:
            continue
        rel = rel or f"work/{it['slug']}.jpg"
        target = IMAGES / rel
        if target.exists() and not force:
            skip += 1
            continue
        try:
            data, ctype = fetch(source_key(src) + "=s0")
        except Exception as e:  # noqa: BLE001 — 한 건 실패해도 나머지는 계속
            print(f"  [실패] {it['slug']}: {e}")
            fail += 1
            continue
        ext = EXT.get(ctype, target.suffix or ".jpg")
        if target.suffix.lower() != ext:
            new_rel = str(Path(rel).with_suffix(ext)).replace("\\", "/")
            # 주석을 보존하려고 YAML 을 다시 쓰지 않고 해당 줄만 바꾼다
            text, n = re.subn(rf"(\n\s*image:\s*){re.escape(it.get('image') or rel)}\s*\n",
                              rf"\g<1>{new_rel}\n", text, count=1)
            changed |= bool(n)
            rel, target = new_rel, IMAGES / new_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        ok += 1
        print(f"  받음  {it['slug']}  {len(data) / 1024 / 1024:.1f}MB  → content/images/{rel}")
        time.sleep(0.3)
    if changed:
        PORTFOLIO.write_text(text, encoding="utf-8")
        print("  portfolio.yaml 의 image 확장자를 실제 파일 형식에 맞게 고쳤습니다.")
    print(f"\n다운로드 완료: 새로 받음 {ok} · 이미 있음 {skip} · 실패 {fail}")


# ─────────────────────────────────────────────
# --scan
# ─────────────────────────────────────────────
class GalleryParser(HTMLParser):
    """googleusercontent 이미지로 연결된 링크(갤러리 항목)를 모은다"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._cur: dict | None = None
        self.links: set[str] = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            href = a.get("href") or ""
            if IMG_HOST in href:
                self._cur = {"source": href, "text": [], "attr_title": a.get("data-title") or a.get("title") or ""}
            elif href:
                self.links.add(href)
        elif tag == "img" and self._cur is not None and not self._cur["attr_title"]:
            self._cur["attr_title"] = a.get("alt") or ""
        elif tag == "br" and self._cur is not None:
            self._cur["text"].append("\n")

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._cur is not None:
            self.items.append(self._cur)
            self._cur = None


def parse_item(raw: dict) -> dict:
    text = html.unescape("".join(raw["text"]))
    lines = [ln.strip(" - \t") for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    title = raw["attr_title"].strip() or (lines[0] if lines else "")
    # Creatorlink 는 제목을 두 번 출력하므로 앞쪽 반복을 걷어낸다
    if lines and lines[0].startswith(title + " " + title):
        lines[0] = lines[0][len(title) * 2 + 1:].strip()
    elif lines and lines[0].startswith(title):
        lines[0] = lines[0][len(title):].strip()
    lines = [ln for ln in lines if ln and ln != title]

    item: dict = {"title": title}
    rest = []
    for ln in lines:
        m = re.match(r"^(컨셉|톤앤매너|구성|품목)\s*:\s*(.+)$", ln)
        if m:
            key = {"컨셉": "concept", "톤앤매너": "tone", "구성": "structure", "품목": "summary"}[m.group(1)]
            val = m.group(2).strip()
            item[key] = [s.strip() for s in val.split("→")] if key == "structure" else val
        else:
            rest.append(ln)
    if rest and "summary" not in item:
        item["summary"] = " ".join(rest)
    item["source"] = source_key(raw["source"]) + "=s0"
    return item


def slugify(title: str, used: set[str]) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    base = ascii_part[:40].strip("-") or "work"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


def scan(site_url: str) -> None:
    site = yaml.safe_load((CONTENT / "site.yaml").read_text(encoding="utf-8"))
    existing = yaml.safe_load(PORTFOLIO.read_text(encoding="utf-8")) or []
    known = {source_key(i["source"]) for i in existing if i.get("source")}
    used = {i["slug"] for i in existing}

    # Creatorlink 메뉴 주소 = 메뉴 이름의 공백을 하이픈으로 바꾼 것
    pages = [(None, site_url + "/")]
    for c in site["categories"]:
        path = urllib.parse.quote(c["name"].replace(" ", "-"))
        pages.append((c["slug"], f"{site_url}/{path}"))

    found: dict[str, dict] = {}
    for cat, url in pages:
        try:
            body, _ = fetch(url)
        except Exception as e:  # noqa: BLE001
            print(f"  [건너뜀] {url}: {e}")
            continue
        p = GalleryParser()
        p.feed(body.decode("utf-8", "replace"))
        n_new = 0
        for raw in p.items:
            key = source_key(raw["source"])
            if key in known:
                continue
            item = found.get(key) or parse_item(raw)
            if cat:                      # 분류 페이지에서 찾은 정보가 우선
                item["category"] = cat
            if key not in found:
                n_new += 1
            found[key] = item
        label = cat or "메인"
        print(f"  {label:<14} 갤러리 {len(p.items):>3}개 · 새 작업물 {n_new}개")
        time.sleep(0.5)

    if not found:
        print("\n새로 추가할 작업물이 없습니다. portfolio.yaml 이 최신입니다.")
        return

    out = []
    for item in found.values():
        slug = slugify(item["title"], used)
        entry = {"slug": slug, "title": item["title"], "category": item.get("category", "detail-page")}
        for k in ("summary", "concept", "tone", "structure"):
            if item.get(k):
                entry[k] = item[k]
        entry["image"] = f"work/{slug}.jpg"
        entry["source"] = item["source"]
        out.append(entry)

    dest = CONTENT / "portfolio.new.yaml"
    header = ("# import_creatorlink.py --scan 결과 — 검토 후 portfolio.yaml 로 옮겨 붙이세요.\n"
              "# slug 는 영문으로 알아보기 쉽게 고치는 것을 권장합니다.\n\n")
    dest.write_text(header + yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=200),
                    encoding="utf-8")
    print(f"\n새 작업물 {len(out)}개를 content/portfolio.new.yaml 에 저장했습니다.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Creatorlink → JIUDESIGN 이전 도구")
    ap.add_argument("--scan", action="store_true", help="Creatorlink 에서 아직 없는 작업물 찾기")
    ap.add_argument("--download", action="store_true", help="원본 이미지 내려받기")
    ap.add_argument("--force", action="store_true", help="이미 받은 이미지도 다시 받기")
    ap.add_argument("--site", default="https://jiudesigner.creatorlink.net")
    args = ap.parse_args()
    if not (args.scan or args.download):
        ap.print_help()
        sys.exit(1)
    if args.scan:
        print("Creatorlink 페이지 살펴보는 중")
        scan(args.site.rstrip("/"))
    if args.download:
        print("원본 이미지 내려받는 중")
        download(args.force)


if __name__ == "__main__":
    main()
