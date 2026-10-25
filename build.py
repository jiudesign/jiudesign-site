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
WORKS_DIR = CONTENT / "works"


def _to_datetime(value) -> dt.datetime | None:
    """관리자 화면이 저장하는 날짜(문자열·date·datetime)를 모두 비교 가능한 datetime 으로"""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        d = value
    elif isinstance(value, dt.date):
        d = dt.datetime(value.year, value.month, value.day)
    else:
        try:
            d = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return d.replace(tzinfo=None) if d.tzinfo is None else d.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _image_path(value) -> str | None:
    """'work/a.png', '/work/a.png', 'content/images/work/a.png' 모두 content/images 기준 경로로"""
    if not value:
        return None
    v = str(value).strip().lstrip("/")
    for prefix in ("content/images/", "images/"):
        if v.startswith(prefix):
            v = v[len(prefix):]
    return v


def _raw_works() -> list[tuple[str, dict, str | None]]:
    """(어디서 읽었는지, 내용, 파일 이름 slug) 목록. content/works/ 가 있으면 그것을, 없으면 portfolio.yaml"""
    if WORKS_DIR.is_dir():
        out = []
        for path in sorted([*WORKS_DIR.glob("*.yml"), *WORKS_DIR.glob("*.yaml")]):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                Log.warn(f"works/{path.name}: 형식 오류라 건너뜀 ({str(e).splitlines()[0]})")
                continue
            if not isinstance(data, dict):
                Log.warn(f"works/{path.name}: 내용이 비어 있거나 형식이 달라 건너뜀")
                continue
            out.append((f"works/{path.name}", data, path.stem.lower()))
        if (CONTENT / "portfolio.yaml").exists():
            Log.warn("content/works/ 를 사용합니다. portfolio.yaml 은 더 이상 읽지 않습니다")
        return out
    items = read_yaml(CONTENT / "portfolio.yaml") or []
    return [(f"portfolio.yaml {i}번째 항목", raw, None) for i, raw in enumerate(items, 1) if isinstance(raw, dict)]


def load_works(site: dict) -> list[Work]:
    """작업물 읽기. 관리자 화면에서 들어온 실수 하나 때문에 배포 전체가 멈추지 않도록,
    문제 있는 작업물은 경고를 남기고 건너뜁니다."""
    cats = {c["slug"]: c for c in site["categories"]}
    subs = {c["slug"]: {s["slug"]: s for s in (c.get("subcategories") or [])} for c in site["categories"]}
    for c in site["categories"]:
        for sc in c.get("subcategories") or []:
            if not SLUG_RE.match(str(sc["slug"])):
                Log.fail(f"site.yaml: 세부 분류 slug '{sc['slug']}' 는 영문 소문자·숫자·하이픈만 쓸 수 있습니다")
    entries: list[tuple[dt.datetime | None, int, Work]] = []
    seen: set[str] = set()
    unsorted: list[str] = []
    for order, (where, raw, file_slug) in enumerate(_raw_works()):
        slug = str(raw.get("slug") or file_slug or "").strip().lower()
        title = str(raw.get("title") or "").strip()
        category = str(raw.get("category") or "").strip()
        if not slug or not title or not category:
            missing = [k for k, v in (("slug", slug), ("제목", title), ("분류", category)) if not v]
            Log.warn(f"{where}: {', '.join(missing)} 이(가) 비어 있어 건너뜀")
            continue
        if not SLUG_RE.match(slug):
            Log.warn(f"{where}: 주소 이름 '{slug}' 에 쓸 수 없는 글자가 있어 건너뜀 (영문 소문자·숫자·-·_ 만 가능)")
            continue
        if slug in seen:
            Log.warn(f"{where}: 주소 이름 '{slug}' 가 다른 작업물과 겹쳐 건너뜀")
            continue
        if category not in cats:
            Log.warn(f"{where}: 분류 '{category}' 가 site.yaml 에 없어 건너뜀 (가능: {', '.join(cats)})")
            continue
        cat_subs = subs[category]
        sub = None
        raw_sub = str(raw.get("subcategory") or "").strip()
        if raw_sub:
            sub = cat_subs.get(raw_sub)
            if not sub:
                Log.warn(f"{where} ({slug}): 세부 분류 '{raw_sub}' 는 '{cats[category]['name']}' 에 없어 무시함")
        if not sub and cat_subs:
            unsorted.append(slug)
        if slug in cat_subs:
            Log.warn(f"{where}: 주소 이름 '{slug}' 가 세부 분류 이름과 같아 건너뜀. 다른 이름으로 바꿔주세요")
            continue
        seen.add(slug)
        structure = raw.get("structure") or []
        if isinstance(structure, str):
            structure = [s.strip() for s in re.split(r"→|\n", structure) if s.strip()]
        work = Work(
            slug=slug, title=title, category=cats[category], subcategory=sub,
            image=_image_path(raw.get("image")), source=raw.get("source"),
            concept=raw.get("concept") or None, tone=raw.get("tone") or None,
            summary=raw.get("summary") or None, client=raw.get("client") or None,
            structure=[str(x.get("item") if isinstance(x, dict) else x) for x in structure if x],
            featured=bool(raw.get("featured")),
        )
        entries.append((_to_datetime(raw.get("date")), order, work))
    if unsorted:
        Log.warn(f"세부 분류가 없는 작업물 {len(unsorted)}개 — 'All' 에만 보입니다: "
                 + ", ".join(unsorted[:8]) + (" …" if len(unsorted) > 8 else ""))
    # 등록일이 최근인 것부터. 등록일이 없으면 파일 순서대로 그 뒤에
    entries.sort(key=lambda e: (e[0] is None, -(e[0].timestamp() if e[0] else 0), e[1]))
    return [w for _, _, w in entries]


FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def load_notes() -> list[Note]:
    notes = []
    for path in sorted((CONTENT / "notes").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        m = FRONT_RE.match(text)
        if not m:
            Log.warn(f"notes/{path.name}: 맨 위 --- 정보가 없어 건너뜀")
            continue
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            Log.warn(f"notes/{path.name}: 형식 오류라 건너뜀")
            continue
        when = _to_datetime(meta.get("date"))
        if not when:
            Log.warn(f"notes/{path.name}: 날짜가 없어 건너뜀")
            continue
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem)
        if not SLUG_RE.match(slug.lower()):
            slug = when.strftime("%Y%m%d") + "-" + hashlib.sha1(path.stem.encode()).hexdigest()[:6]
        notes.append(Note(
            slug=slug.lower(),
            title=str(meta.get("title") or slug),
            date=when.date(),
            summary=str(meta.get("summary") or ""),
            html=markdown.markdown(m.group(2), extensions=["extra", "sane_lists"]),
        ))
    return sorted(notes, key=lambda n: n.date, reverse=True)


def load_services(site: dict) -> list[dict]:
    """제작 서비스 안내 페이지 (content/services.yaml). 없으면 서비스 페이지를 만들지 않음"""
    path = CONTENT / "services.yaml"
    if not path.exists():
        return []
    data = read_yaml(path) or []
    if isinstance(data, dict):
        data = data.get("items") or []
    cats = {c["slug"]: c for c in site["categories"]}
    out = []
    for sv in data:
        if not isinstance(sv, dict) or not sv.get("slug") or not sv.get("name"):
            continue
        slug = str(sv["slug"]).strip().lower()
        if not SLUG_RE.match(slug):
            Log.warn(f"services.yaml: 주소 이름 '{slug}' 에 쓸 수 없는 글자가 있어 건너뜀")
            continue
        lst = lambda k: [x for x in (sv.get(k) or []) if x]
        out.append({
            **sv, "slug": slug, "url": f"/services/{slug}/",
            "headline": sv.get("headline") or sv["name"],
            "lead": sv.get("lead") or "", "description": sv.get("description") or sv.get("lead") or "",
            "category": cats.get(str(sv.get("category") or "")),
            "points": [p for p in lst("points") if isinstance(p, dict) and p.get("title")],
            "process": [str(x.get("item") if isinstance(x, dict) else x) for x in lst("process")],
            "faq": [f for f in lst("faq") if isinstance(f, dict) and f.get("q") and f.get("a")],
        })
        out[-1]["faq_ld"] = {
            "@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": str(f["q"]),
                            "acceptedAnswer": {"@type": "Answer", "text": str(f["a"])}} for f in out[-1]["faq"]],
        } if out[-1]["faq"] else None
    return out


def load_products() -> list[dict]:
    data = read_yaml(CONTENT / "products.yaml") or []
    if isinstance(data, dict):           # 관리자 화면 형식: items: [...]
        data = data.get("items") or []
    out = []
    for p in data:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        inc = p.get("includes") or []
        p = {**p, "includes": [str(x.get("item") if isinstance(x, dict) else x) for x in inc if x],
             "price": p.get("price") or "문의", "note": p.get("note") or ""}
        out.append(p)
    return out


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
# 관리자 화면 (/admin) 설정 — site.yaml 의 분류가 바뀌면 선택 목록도 자동으로 따라갑니다
# ─────────────────────────────────────────────
def write_cms_config(site: dict) -> None:
    cms = site.get("cms") or {}
    repo = cms.get("repo")
    if not repo:
        Log.warn("site.yaml 에 cms.repo 가 없어 관리자 화면(/admin)을 만들지 않았습니다")
        shutil.rmtree(DIST / "admin", ignore_errors=True)
        return
    cat_opts = [{"label": c["name"], "value": c["slug"]} for c in site["categories"]]
    sub_opts = [{"label": f"{sc['name']}  ({c['name']})", "value": sc["slug"]}
                for c in site["categories"] for sc in (c.get("subcategories") or [])]
    slug_rule = ["^[a-z0-9][a-z0-9_-]*$", "영문 소문자·숫자·하이픈(-)·밑줄(_)만 쓸 수 있습니다"]
    config = {
        "backend": {"name": "github", "repo": repo, "branch": cms.get("branch", "main"),
                    "commit_messages": {
                        "create": "관리자: {{collection}} 추가 — {{slug}}",
                        "update": "관리자: {{collection}} 수정 — {{slug}}",
                        "delete": "관리자: {{collection}} 삭제 — {{slug}}",
                        "uploadMedia": "관리자: 이미지 업로드 — {{path}}",
                        "deleteMedia": "관리자: 이미지 삭제 — {{path}}"}},
        "site_url": site["base_url"],
        "display_url": site["base_url"],
        "logo_url": "/favicon.svg",
        "media_folder": "content/images/work",
        "public_folder": "work",
        "slug": {"encoding": "ascii", "clean_accents": True, "sanitize_replacement": "-"},
        "collections": [
            {
                "name": "works", "label": "작업물", "label_singular": "작업물",
                "description": "저장하면 몇 분 뒤 사이트에 반영됩니다. 등록일이 최근인 작업물이 앞에 보입니다.",
                "folder": "content/works", "extension": "yml", "format": "yaml",
                "create": True, "delete": True,
                "slug": "{{year}}{{month}}{{day}}-{{hour}}{{minute}}{{second}}",
                "identifier_field": "title",
                "summary": "{{title}}",
                "sortable_fields": ["date", "title", "category"],
                "fields": [
                    {"label": "제목", "name": "title", "widget": "string"},
                    {"label": "대분류", "name": "category", "widget": "select",
                     "options": cat_opts, "default": cat_opts[0]["value"]},
                    {"label": "세부 분류", "name": "subcategory", "widget": "select", "required": False,
                     "options": sub_opts,
                     "hint": "상세페이지 디자인일 때만 고르세요. 비워두면 All 에만 보입니다."},
                    {"label": "이미지", "name": "image", "widget": "image",
                     "hint": "상세페이지 원본(세로로 긴 이미지) 그대로 올리면 자동으로 변환·분할됩니다. 파일 하나 25MB 이하."},
                    {"label": "컨셉", "name": "concept", "widget": "string", "required": False},
                    {"label": "톤앤매너", "name": "tone", "widget": "string", "required": False},
                    {"label": "작업 내용", "name": "summary", "widget": "text", "required": False},
                    {"label": "의뢰처", "name": "client", "widget": "string", "required": False},
                    {"label": "구성 순서", "name": "structure", "widget": "list", "required": False,
                     "field": {"label": "항목", "name": "item", "widget": "string"},
                     "hint": "상세페이지 구성을 순서대로 하나씩 추가하세요."},
                    {"label": "메인 화면 대표 작업", "name": "featured", "widget": "boolean",
                     "default": False, "required": False,
                     "hint": "켜면 메인 화면 오른쪽 휴대폰 화면에서 흘러갑니다. 하나만 켜세요."},
                    {"label": "등록일", "name": "date", "widget": "datetime", "default": "{{now}}",
                     "hint": "날짜가 최근일수록 앞에 보입니다. 순서를 바꾸고 싶으면 날짜를 고치세요."},
                    {"label": "주소 이름 (선택)", "name": "slug", "widget": "string", "required": False,
                     "pattern": slug_rule,
                     "hint": "사이트 주소에 쓰이는 영문 이름입니다. 예: honey-gift. 비워두면 자동으로 정해집니다."},
                ],
            },
            {
                "name": "notes", "label": "디자인 노트", "label_singular": "노트",
                "folder": "content/notes", "extension": "md", "format": "frontmatter",
                "create": True, "delete": True,
                "slug": "{{year}}-{{month}}-{{day}}-{{hour}}{{minute}}{{second}}",
                "identifier_field": "title", "sortable_fields": ["date", "title"],
                "fields": [
                    {"label": "제목", "name": "title", "widget": "string"},
                    {"label": "날짜", "name": "date", "widget": "datetime", "default": "{{now}}",
                     "time_format": False, "date_format": "YYYY-MM-DD"},
                    {"label": "한 줄 요약", "name": "summary", "widget": "string", "required": False},
                    {"label": "본문", "name": "body", "widget": "markdown"},
                ],
            },
            {
                "name": "services", "label": "제작 서비스 안내", "files": [{
                    "name": "services", "label": "제작 서비스 페이지", "file": "content/services.yaml",
                    "description": "검색에 나오는 '상세페이지 제작', '배달앱 배너 제작' 안내 페이지입니다.",
                    "fields": [{
                        "label": "서비스", "name": "items", "widget": "list", "summary": "{{fields.name}}",
                        "fields": [
                            {"label": "주소 이름 (영문)", "name": "slug", "widget": "string", "pattern": slug_rule,
                             "hint": "사이트 주소 /services/이름/ 에 쓰입니다. 바꾸면 검색에 쌓인 주소가 달라지니 되도록 그대로 두세요."},
                            {"label": "서비스 이름 (검색어)", "name": "name", "widget": "string",
                             "hint": "사람들이 검색하는 말 그대로. 예: 상세페이지 제작"},
                            {"label": "큰 제목", "name": "headline", "widget": "string", "required": False},
                            {"label": "소개 글", "name": "lead", "widget": "text"},
                            {"label": "검색 결과 설명 (150자 이내)", "name": "description", "widget": "text",
                             "required": False, "hint": "네이버·구글 검색 결과에 제목 아래 보이는 문장입니다."},
                            {"label": "함께 보여줄 포트폴리오 분류", "name": "category", "widget": "select",
                             "options": cat_opts, "required": False},
                            {"label": "강점", "name": "points", "widget": "list", "required": False,
                             "summary": "{{fields.title}}",
                             "fields": [{"label": "제목", "name": "title", "widget": "string"},
                                        {"label": "설명", "name": "text", "widget": "text"}]},
                            {"label": "제작 과정", "name": "process", "widget": "list", "required": False,
                             "field": {"label": "단계", "name": "item", "widget": "string"}},
                            {"label": "자주 묻는 질문", "name": "faq", "widget": "list", "required": False,
                             "summary": "{{fields.q}}",
                             "fields": [{"label": "질문", "name": "q", "widget": "string"},
                                        {"label": "답변", "name": "a", "widget": "text"}]},
                        ],
                    }],
                }],
            },
            {
                "name": "settings", "label": "디자인 상품", "files": [{
                    "name": "products", "label": "디자인 상품 목록", "file": "content/products.yaml",
                    "fields": [{
                        "label": "상품", "name": "items", "widget": "list",
                        "summary": "{{fields.name}} — {{fields.price}}",
                        "fields": [
                            {"label": "상품 이름", "name": "name", "widget": "string"},
                            {"label": "가격", "name": "price", "widget": "string", "default": "문의",
                             "hint": "예: 30만원부터, 문의"},
                            {"label": "포함 내용", "name": "includes", "widget": "list", "required": False,
                             "field": {"label": "항목", "name": "item", "widget": "string"}},
                            {"label": "참고 문구", "name": "note", "widget": "string", "required": False},
                        ],
                    }],
                }],
            },
        ],
    }
    out = DIST / "admin"
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.yml").write_text(
        "# build.py 가 site.yaml 을 보고 자동으로 만드는 파일입니다. 직접 고치지 마세요.\n"
        + yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")


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
    products = load_products()
    services = load_services(site)
    Log.info(f"작업물 {len(works)}개 · 노트 {len(notes)}개 · 상품 {len(products)}개 · 서비스 {len(services)}개")

    shutil.rmtree(DIST, ignore_errors=True)
    DIST.mkdir(parents=True)

    print("이미지 처리 중")
    works = attach_pictures(works, cfg, mode)
    if not works:
        Log.fail("사이트에 올릴 작업물이 없습니다. content/images/ 에 이미지가 있는지 확인하세요")

    print("페이지 생성 중")
    assets = copy_static()
    write_cms_config(site)
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
        services=services, seo=site.get("seo") or {},
    )
    env.filters["abs"] = lambda u: u if (u or "").startswith("http") else base_url + (u or "")
    seo = site.get("seo") or {}
    og_abs = (base_url + featured.picture.thumb) if featured and featured.picture and not featured.picture.thumb.startswith("http") \
        else (featured.picture.thumb if featured and featured.picture else base_url + "/favicon.svg")
    env.globals["business_ld"] = {
        "@context": "https://schema.org",
        "@type": "ProfessionalService",
        "@id": base_url + "/#business",
        "name": site["title"],
        "alternateName": site["name"],
        "url": base_url + "/",
        "telephone": (site.get("contact") or {}).get("phone", ""),
        "image": og_abs,
        "description": seo.get("home_description") or site["description"],
        "areaServed": {"@type": "Country", "name": "대한민국"},
        "knowsAbout": seo.get("keywords") or [],
        "makesOffer": [{"@type": "Offer", "itemOffered": {
            "@type": "Service", "name": sv["name"], "url": base_url + sv["url"],
            "description": sv["description"]}} for sv in services],
    }
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
    if services:
        render("services.html", "/services/")
        for sv in services:
            render("service.html", sv["url"], service=sv,
                   works=(by_cat[sv["category"]["slug"]][:8] if sv["category"] else []))
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
    write_rss(site, base_url, works, notes)
    (DIST / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nDisallow: /admin/\nSitemap: {base_url}/sitemap.xml\n")
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


def write_rss(site: dict, base_url: str, works: list[Work], notes: list[Note]) -> None:
    """네이버 서치어드바이저에 제출할 RSS — 최근 노트와 작업물"""
    from email.utils import format_datetime
    from xml.sax.saxutils import escape
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for n in notes[:20]:
        when = dt.datetime(n.date.year, n.date.month, n.date.day, tzinfo=dt.timezone.utc)
        rows.append((when, n.title, base_url + n.url, n.summary))
    for w in works[:30]:
        desc = " ".join(x for x in (w.category["name"], w.subcategory["name"] if w.subcategory else "", w.lead) if x)
        rows.append((now, w.title, base_url + w.url, desc))
    items = "".join(
        f"<item><title>{escape(t)}</title><link>{escape(u)}</link><guid>{escape(u)}</guid>"
        f"<description>{escape(d)}</description><pubDate>{format_datetime(when)}</pubDate></item>\n"
        for when, t, u, d in rows)
    (DIST / "rss.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n'
        f"<title>{escape(site['title'])}</title><link>{escape(base_url)}/</link>"
        f"<description>{escape((site.get('seo') or {}).get('home_description') or site['description'])}</description>"
        f"<language>ko</language>\n{items}</channel></rss>\n", encoding="utf-8")


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
