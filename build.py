#!/usr/bin/env python3
"""
JIUDESIGN 정적 사이트 빌더

    python build.py            # dist/ 에 사이트 생성
    python build.py --serve    # 생성 후 http://localhost:8000 미리보기
    python build.py --remote   # 이미지가 없는 작업물은 Creatorlink 원본으로 임시 표시
    python build.py --strict   # 이미지가 없는 작업물이 하나라도 있으면 실패
    python build.py --clean    # 이미지 캐시까지 지우고 처음부터 다시 생성

기본 동작: 이미지 파일이 없는 작업물은 빼고 나머지로 사이트를 만듭니다 (경고 출력).

content/ 의 YAML·Markdown·이미지를 읽어 templates/ 로 HTML을 만들고,
이미지는 WebP로 변환·리사이즈·분할해서 dist/assets/ 에 넣습니다.
처리한 이미지는 .cache/ 에 저장되어 다음 빌드부터는 바뀐 파일만 다시 처리합니다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import hashlib
import http.server
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
IMAGES = CONTENT / "images"
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
DIST = ROOT / "dist"
CACHE = ROOT / ".cache" / "images"

# Cloudflare Pages 무료 플랜 제한
MAX_ASSET_BYTES = 25 * 1024 * 1024
MAX_FILES = 20_000

# 상세페이지는 세로 수만 px 이미지가 흔하므로 Pillow 의 폭탄 이미지 경고를 끈다
Image.MAX_IMAGE_PIXELS = None


# ─────────────────────────────────────────────
# 로그
# ─────────────────────────────────────────────
class Log:
    warnings: list[str] = []

    @staticmethod
    def info(msg: str) -> None:
        print(f"  {msg}")

    @classmethod
    def warn(cls, msg: str) -> None:
        cls.warnings.append(msg)
        print(f"  [경고] {msg}")

    @staticmethod
    def fail(msg: str) -> None:
        print(f"\n[오류] {msg}", file=sys.stderr)
        sys.exit(1)


# ─────────────────────────────────────────────
# 데이터 모델
# ─────────────────────────────────────────────
@dataclass
class Picture:
    thumb: str
    thumb_w: int
    thumb_h: int
    slices: list[dict]          # [{"url", "w", "h"}]
    width: int
    height: int
    remote: bool = False


@dataclass
class Work:
    slug: str
    title: str
    category: dict
    subcategory: dict | None = None
    image: str | None = None
    source: str | None = None
    concept: str | None = None
    tone: str | None = None
    summary: str | None = None
    client: str | None = None
    structure: list[str] = field(default_factory=list)
    featured: bool = False
    picture: Picture | None = None

    @property
    def url(self) -> str:
        return f"/work/{self.category['slug']}/{self.slug}/"

    @property
    def lead(self) -> str:
        return self.concept or self.summary or ""


@dataclass
class Note:
    slug: str
    title: str
    date: dt.date
    summary: str
    html: str

    @property
    def url(self) -> str:
        return f"/notes/{self.slug}/"


# ─────────────────────────────────────────────
# 콘텐츠 읽기
# ─────────────────────────────────────────────
def read_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        Log.fail(f"{path.relative_to(ROOT)} 형식 오류\n{e}")


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def load_works(site: dict) -> list[Work]:
    cats = {c["slug"]: c for c in site["categories"]}
    subs = {c["slug"]: {s["slug"]: s for s in (c.get("subcategories") or [])} for c in site["categories"]}
    for c in site["categories"]:   # 세부 분류 주소가 작업물 주소와 겹치지 않도록 미리 확인용
        for sc in c.get("subcategories") or []:
            if not SLUG_RE.match(str(sc["slug"])):
                Log.fail(f"site.yaml: 세부 분류 slug '{sc['slug']}' 는 영문 소문자·숫자·하이픈만 쓸 수 있습니다")
    works, seen = [], set()
    unsorted: list[str] = []
    for i, raw in enumerate(read_yaml(CONTENT / "portfolio.yaml"), 1):
        where = f"portfolio.yaml {i}번째 항목"
        for key in ("slug", "title", "category"):
            if not raw.get(key):
                Log.fail(f"{where}: '{key}' 가 비어 있습니다")
        slug = str(raw["slug"])
        if not SLUG_RE.match(slug):
            Log.fail(f"{where}: slug '{slug}' 는 영문 소문자·숫자·하이픈(-)·밑줄(_)만 쓸 수 있습니다")
        if slug in seen:
            Log.fail(f"{where}: slug '{slug}' 가 중복됩니다")
        if raw["category"] not in cats:
            Log.fail(f"{where}: category '{raw['category']}' 가 site.yaml 에 없습니다 "
                     f"(가능: {', '.join(cats)})")
        cat_subs = subs[raw["category"]]
        sub = None
        if raw.get("subcategory"):
            sub = cat_subs.get(str(raw["subcategory"]))
            if not sub:
                opts = ", ".join(cat_subs) or "(이 분류에는 세부 분류가 없습니다)"
                Log.fail(f"{where} ({slug}): subcategory '{raw['subcategory']}' 가 없습니다 (가능: {opts})")
        elif cat_subs:
            unsorted.append(slug)
        if slug in cat_subs:
            Log.fail(f"{where}: slug '{slug}' 가 세부 분류 이름과 같아 주소가 겹칩니다. 다른 slug 로 바꿔주세요")
        seen.add(slug)
        works.append(Work(
            slug=slug,
            title=str(raw["title"]),
            category=cats[raw["category"]],
            subcategory=sub,
            image=raw.get("image"),
            source=raw.get("source"),
            concept=raw.get("concept"),
            tone=raw.get("tone"),
            summary=raw.get("summary"),
            client=raw.get("client"),
            structure=[str(s) for s in (raw.get("structure") or [])],
            featured=bool(raw.get("featured")),
        ))
    if unsorted:
        Log.warn(f"세부 분류(subcategory)가 없는 작업물 {len(unsorted)}개 — 'All' 에만 보입니다: "
                 + ", ".join(unsorted[:8]) + (" …" if len(unsorted) > 8 else ""))
    return works


FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)


def load_notes() -> list[Note]:
    notes = []
    for path in sorted((CONTENT / "notes").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        m = FRONT_RE.match(text)
        if not m:
            Log.fail(f"notes/{path.name}: 맨 위에 --- 로 감싼 title/date 정보가 필요합니다")
        meta = yaml.safe_load(m.group(1)) or {}
        date = meta.get("date")
        if isinstance(date, str):
            date = dt.date.fromisoformat(date)
        if not isinstance(date, dt.date):
            Log.fail(f"notes/{path.name}: date 는 2026-10-25 형식이어야 합니다")
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem)
        notes.append(Note(
            slug=slug,
            title=str(meta.get("title") or slug),
            date=date,
            summary=str(meta.get("summary") or ""),
            html=markdown.markdown(m.group(2), extensions=["extra", "sane_lists"]),
        ))
    return sorted(notes, key=lambda n: n.date, reverse=True)


# ─────────────────────────────────────────────
# 이미지 처리
# ─────────────────────────────────────────────
def _save_webp(img: Image.Image, path: Path, quality: int) -> None:
    """25MiB 를 넘지 않을 때까지 품질을 낮춰 저장"""
    q = quality
    while True:
        img.save(path, "WEBP", quality=q, method=5)
        if path.stat().st_size <= MAX_ASSET_BYTES * 0.95 or q <= 40:
            return
        q -= 10


def _normalize(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        return img.convert("RGBA")
    return img.convert("RGB")


def process_image(src: Path, cfg: dict) -> tuple[Path, dict]:
    """원본 → (캐시 폴더, manifest). 원본과 설정이 같으면 캐시를 그대로 사용"""
    digest = hashlib.sha1(src.read_bytes())
    digest.update(json.dumps(cfg, sort_keys=True).encode())
    key = digest.hexdigest()[:12]
    out = CACHE / key
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        return out, json.loads(manifest_path.read_text())

    tmp = out.with_suffix(".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    with Image.open(src) as opened:
        img = _normalize(opened)
    w, h = img.size
    if w > cfg["full_width"]:
        h = round(h * cfg["full_width"] / w)
        w = cfg["full_width"]
        img = img.resize((w, h), Image.LANCZOS)

    slices = []
    step = cfg["slice_height"]
    for n, top in enumerate(range(0, h, step), 1):
        part = img.crop((0, top, w, min(top + step, h)))
        name = f"{key}-{n:02d}.webp"
        _save_webp(part, tmp / name, cfg["quality"])
        slices.append({"file": name, "w": part.width, "h": part.height})

    tw, th = cfg["thumb_width"], cfg["thumb_height"]
    # 세로로 긴 이미지는 상단(메인 비주얼) 기준, 가로형 배너는 가운데 기준으로 자름
    centering = (0.5, 0.0) if h / w > th / tw else (0.5, 0.5)
    thumb = ImageOps.fit(img, (tw, th), Image.LANCZOS, centering=centering)
    _save_webp(thumb, tmp / f"{key}-thumb.webp", cfg["quality"])

    manifest = {"key": key, "width": w, "height": h, "slices": slices,
                "thumb": f"{key}-thumb.webp", "thumb_w": tw, "thumb_h": th}
    (tmp / "manifest.json").write_text(json.dumps(manifest))
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return out, manifest


def remote_picture(url: str, cfg: dict) -> Picture:
    """로컬 이미지가 아직 없을 때 Creatorlink 원본을 임시로 사용 (Google 이미지 서버 크기 파라미터)"""
    base = url.split("=")[0]
    tw, th = cfg["thumb_width"], cfg["thumb_height"]
    return Picture(
        thumb=f"{base}=w{tw}-h{th}-p",   # -p: 비율 유지하며 채워 자르기
        thumb_w=tw, thumb_h=th,
        slices=[{"url": f"{base}=w{cfg['full_width']}", "w": 0, "h": 0}],
        width=0, height=0, remote=True,
    )


def attach_pictures(works: list[Work], cfg: dict, mode: str) -> list[Work]:
    """이미지를 붙이고, 사이트에 올릴 작업물만 돌려준다.

    로컬 이미지가 없는 작업물은
      mode="skip"   (기본) 사이트에서 빼고 경고만 출력 → 이미지를 넣으면 다음 빌드에 자동으로 나타남
      mode="remote" Creatorlink 원본 주소를 임시로 연결 (이전 작업 중 미리보기용)
      mode="strict" 빌드 실패
    """
    dest_root = DIST / "assets" / "work"
    kept: list[Work] = []
    for work in works:
        src = IMAGES / work.image if work.image else None
        if src and src.is_file():
            try:
                cache_dir, m = process_image(src, cfg)
            except Exception as e:  # noqa: BLE001 — 깨진 파일 하나 때문에 전체 배포가 멈추지 않게
                if mode == "strict":
                    Log.fail(f"{work.slug}: 이미지를 열 수 없습니다 ({e})")
                Log.warn(f"{work.slug}: content/images/{work.image} 를 열 수 없어 사이트에서 뺐습니다 ({e})")
                continue
            dest = dest_root / work.slug
            dest.mkdir(parents=True, exist_ok=True)
            for f in [m["thumb"], *(s["file"] for s in m["slices"])]:
                shutil.copy2(cache_dir / f, dest / f)
            prefix = f"/assets/work/{work.slug}/"
            work.picture = Picture(
                thumb=prefix + m["thumb"], thumb_w=m["thumb_w"], thumb_h=m["thumb_h"],
                slices=[{"url": prefix + s["file"], "w": s["w"], "h": s["h"]} for s in m["slices"]],
                width=m["width"], height=m["height"],
            )
            Log.info(f"이미지  {work.slug}  {m['width']}×{m['height']}px → {len(m['slices'])}조각")
            kept.append(work)
            continue

        missing = f"content/images/{work.image}" if work.image else "image 항목"
        if mode == "strict":
            Log.fail(f"{work.slug}: {missing} 없음")
        if mode == "remote" and work.source:
            Log.warn(f"{work.slug}: {missing} 없음 → Creatorlink 원본을 임시로 표시")
            work.picture = remote_picture(work.source, cfg)
            kept.append(work)
        else:
            Log.warn(f"{work.slug}: {missing} 없음 → 이번 배포에서 제외 (파일을 넣으면 다시 나타납니다)")
    return kept


# ─────────────────────────────────────────────
# 정적 파일 (CSS/JS 는 파일명에 해시를 붙여 브라우저 캐시를 안전하게 오래 유지)
# ─────────────────────────────────────────────
def copy_static() -> dict[str, str]:
    mapping = {}
    for path in STATIC.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(STATIC)
        if path.suffix in (".css", ".js"):
            h = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
            out_rel = rel.with_name(f"{rel.stem}.{h}{rel.suffix}")
            target = DIST / "assets" / out_rel
            url = f"/assets/{out_rel.as_posix()}"
        elif rel.parts[0] == "root":          # static/root/* → 사이트 루트 (favicon 등)
            out_rel = Path(*rel.parts[1:])
            target = DIST / out_rel
            url = f"/{out_rel.as_posix()}"
        else:
            target = DIST / "assets" / rel
            url = f"/assets/{rel.as_posix()}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        mapping[rel.as_posix()] = url
    return mapping


# ─────────────────────────────────────────────
# 페이지 생성
# ─────────────────────────────────────────────
def build(mode: str = "skip") -> None:
    started = dt.datetime.now()
    site = read_yaml(CONTENT / "site.yaml")
    cfg = site["images"]

    print("콘텐츠 읽는 중")
    works = load_works(site)
    notes = load_notes()
    products = read_yaml(CONTENT / "products.yaml")
    Log.info(f"작업물 {len(works)}개 · 노트 {len(notes)}개 · 상품 {len(products)}개")

    shutil.rmtree(DIST, ignore_errors=True)
    DIST.mkdir(parents=True)

    print("이미지 처리 중")
    works = attach_pictures(works, cfg, mode)
    if not works:
        Log.fail("사이트에 올릴 작업물이 없습니다. content/images/ 에 이미지가 있는지 확인하세요")

    print("페이지 생성 중")
    assets = copy_static()
    featured = next((w for w in works if w.featured), works[0] if works else None)
    by_cat = {c["slug"]: [w for w in works if w.category["slug"] == c["slug"]]
              for c in site["categories"]}
    by_sub = {(c["slug"], sc["slug"]): [w for w in by_cat[c["slug"]]
                                        if w.subcategory and w.subcategory["slug"] == sc["slug"]]
              for c in site["categories"] for sc in (c.get("subcategories") or [])}

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    base_url = site["base_url"].rstrip("/")
    env.globals.update(
        site=site, asset=lambda name: assets[name], categories=site["categories"],
        by_cat=by_cat, by_sub=by_sub, year=dt.date.today().year, base_url=base_url,
        og_image=(featured.picture.thumb if featured and featured.picture else ""),
    )
    env.filters["tel"] = lambda s: re.sub(r"[^0-9+]", "", s or "")
    env.filters["kdate"] = lambda d: f"{d.year}. {d.month}. {d.day}."

    urls: list[str] = []

    def render(template: str, url: str, **ctx) -> None:
        html = env.get_template(template).render(page_url=url, **ctx)
        out = DIST / url.lstrip("/") / "index.html" if url.endswith("/") else DIST / url.lstrip("/")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        if url.endswith("/"):
            urls.append(url)

    render("index.html", "/", works=works, featured=featured, notes=notes[:3])
    render("category.html", "/work/", category=None, sub=None, works=works)
    for cat in site["categories"]:
        items = by_cat[cat["slug"]]
        render("category.html", f"/work/{cat['slug']}/", category=cat, sub=None, works=items)
        for sc in cat.get("subcategories") or []:
            render("category.html", f"/work/{cat['slug']}/{sc['slug']}/", category=cat, sub=sc,
                   works=[w for w in items if w.subcategory and w.subcategory["slug"] == sc["slug"]])
        for i, work in enumerate(items):
            render("work.html", work.url, work=work,
                   prev=items[i - 1] if i > 0 else None,
                   next=items[i + 1] if i + 1 < len(items) else None,
                   related=([w for w in items if w is not work and work.subcategory
                             and w.subcategory is work.subcategory]
                            + [w for w in items if w is not work and not (work.subcategory
                               and w.subcategory is work.subcategory)])[:3])
    render("notes.html", "/notes/", notes=notes)
    for note in notes:
        render("note.html", note.url, note=note)
    render("products.html", "/products/", products=products)
    render("404.html", "/404.html")

    # 검색엔진용 파일
    today = dt.date.today().isoformat()
    (DIST / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{base_url}{u}</loc><lastmod>{today}</lastmod></url>\n" for u in urls)
        + "</urlset>\n", encoding="utf-8")
    (DIST / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n")
    # Cloudflare Pages 캐시 헤더: 해시가 붙은 파일은 1년, HTML 은 매번 확인
    (DIST / "_headers").write_text(
        "/assets/*\n  Cache-Control: public, max-age=31536000, immutable\n"
        "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n")

    check_limits()
    secs = (dt.datetime.now() - started).total_seconds()
    print(f"\n완료  dist/  ({len(urls)}개 페이지, {secs:.1f}초)")
    if Log.warnings:
        print(f"경고 {len(Log.warnings)}건 — 위 내용을 확인하세요. "
              f"이미지가 없는 작업물은 content/images/ 에 파일을 넣거나 "
              f"`python tools/import_creatorlink.py --download` 로 받을 수 있습니다.")


def check_limits() -> None:
    files = [p for p in DIST.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    big = [p for p in files if p.stat().st_size > MAX_ASSET_BYTES]
    if len(files) > MAX_FILES:
        Log.fail(f"파일 {len(files)}개 — Cloudflare Pages 무료 한도({MAX_FILES}개)를 넘었습니다")
    if big:
        Log.fail("25MiB 를 넘는 파일: " + ", ".join(str(p.relative_to(DIST)) for p in big))
    Log.info(f"배포 크기 {total / 1024 / 1024:.1f}MB · 파일 {len(files)}개 (한도 {MAX_FILES}개)")


def serve(port: int) -> None:
    class Handler(http.server.SimpleHTTPRequestHandler):
        def send_error(self, code, message=None, explain=None):
            if code == 404 and (DIST / "404.html").exists():
                body = (DIST / "404.html").read_bytes()
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().send_error(code, message, explain)

    handler = functools.partial(Handler, directory=str(DIST))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"미리보기: http://localhost:{port}  (종료: Ctrl+C)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="JIUDESIGN 정적 사이트 빌더")
    ap.add_argument("--serve", action="store_true", help="빌드 후 로컬 미리보기 서버 실행")
    ap.add_argument("--port", type=int, default=8000)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--strict", action="store_true", help="로컬 이미지가 없는 작업물이 있으면 실패")
    g.add_argument("--remote", action="store_true", help="로컬 이미지가 없으면 Creatorlink 원본을 임시로 표시")
    ap.add_argument("--clean", action="store_true", help="이미지 캐시를 지우고 다시 생성")
    args = ap.parse_args()
    if args.clean:
        shutil.rmtree(CACHE, ignore_errors=True)
    build(mode="strict" if args.strict else "remote" if args.remote else "skip")
    if args.serve:
        serve(args.port)


if __name__ == "__main__":
    main()
