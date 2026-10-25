#!/usr/bin/env python3
"""
관리자 화면(/admin)용으로 콘텐츠 구조를 바꾸는 도구 (한 번만 실행)

    python tools/migrate_to_cms.py          # 미리보기: 무엇이 바뀌는지만 보여줌
    python tools/migrate_to_cms.py --apply  # 실제로 변환

하는 일
  1) content/portfolio.yaml  →  content/works/<slug>.yml  (작업물마다 파일 하나)
     지금 보이는 순서가 유지되도록 '등록일'을 1분 간격으로 넣습니다.
     원래 파일은 content/portfolio.yaml.bak 으로 남깁니다 (GitHub 에는 올라가지 않음).
  2) content/products.yaml   →  관리자 화면 형식(items: 목록)으로 바꿈
  3) content/site.yaml       →  관리자 화면이 연결할 GitHub 저장소(cms:) 추가
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
PORTFOLIO = CONTENT / "portfolio.yaml"
WORKS = CONTENT / "works"
PRODUCTS = CONTENT / "products.yaml"
SITE = CONTENT / "site.yaml"
GITIGNORE = ROOT / ".gitignore"

FIELD_ORDER = ["title", "category", "subcategory", "image", "concept", "tone", "summary",
               "client", "structure", "featured", "date", "slug"]
KST = dt.timezone(dt.timedelta(hours=9))


def detect_repo() -> str | None:
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def dump(data: dict) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=200)


def main() -> None:
    ap = argparse.ArgumentParser(description="관리자 화면용 구조 변환")
    ap.add_argument("--apply", action="store_true", help="실제로 변환")
    ap.add_argument("--repo", help="GitHub 저장소 (예: jiudesign/jiudesign-site). 비우면 git 설정에서 찾음")
    args = ap.parse_args()
    plan: list[str] = []
    writes: list[tuple[Path, str]] = []

    # 1) 작업물
    if PORTFOLIO.exists():
        items = yaml.safe_load(PORTFOLIO.read_text(encoding="utf-8")) or []
        base = dt.datetime.now(KST).replace(second=0, microsecond=0)
        made = skipped = 0
        for i, it in enumerate(items):
            if not isinstance(it, dict) or not it.get("slug"):
                continue
            slug = str(it["slug"]).strip().lower()
            target = WORKS / f"{slug}.yml"
            if target.exists():
                skipped += 1
                continue
            data = {k: v for k, v in it.items() if k not in ("slug", "source") and v not in (None, "", [])}
            data.setdefault("date", (base - dt.timedelta(minutes=i)).isoformat())
            ordered = {k: data[k] for k in FIELD_ORDER if k in data}
            ordered.update({k: v for k, v in data.items() if k not in ordered})
            writes.append((target, dump(ordered)))
            made += 1
        plan.append(f"작업물 {made}개 → content/works/ 파일로 변환" + (f" (이미 있는 {skipped}개는 건너뜀)" if skipped else ""))
        plan.append("content/portfolio.yaml → portfolio.yaml.bak 로 이름 변경")
    elif WORKS.is_dir():
        plan.append("작업물: 이미 content/works/ 구조입니다 (건너뜀)")
    else:
        sys.exit("content/portfolio.yaml 도 content/works/ 도 없습니다. 사이트 폴더에서 실행했는지 확인하세요.")

    # 2) 디자인 상품
    if PRODUCTS.exists():
        prod = yaml.safe_load(PRODUCTS.read_text(encoding="utf-8")) or []
        if isinstance(prod, list):
            header = "# 디자인 상품 — 관리자 화면(/admin)의 '디자인 상품'에서 고칠 수 있습니다.\n"
            writes.append((PRODUCTS, header + dump({"items": prod})))
            plan.append(f"디자인 상품 {len(prod)}개 → 관리자 화면 형식으로 변환")
        else:
            plan.append("디자인 상품: 이미 관리자 화면 형식입니다 (건너뜀)")

    # 3) site.yaml 에 저장소 연결 정보
    site_text = SITE.read_text(encoding="utf-8")
    if re.search(r"(?m)^cms:", site_text):
        plan.append("site.yaml: cms 설정이 이미 있습니다 (건너뜀)")
    else:
        repo = args.repo or detect_repo()
        if not repo:
            sys.exit("GitHub 저장소를 찾지 못했습니다. --repo 사용자명/저장소이름 을 붙여 다시 실행하세요.")
        block = ("\n# 관리자 화면(/admin)이 연결할 GitHub 저장소\n"
                 f"cms:\n  repo: {repo}\n  branch: main\n")
        writes.append((SITE, site_text.rstrip("\n") + "\n" + block))
        plan.append(f"site.yaml: 관리자 화면 연결 저장소 = {repo}")

    print("\n".join("  - " + p for p in plan))
    for path, text in writes:      # 쓰기 전에 모두 올바른 YAML 인지 확인
        if path.suffix in (".yml", ".yaml"):
            yaml.safe_load(text)

    if not args.apply:
        print("\n미리보기입니다. 위 내용대로 바꾸려면:  python tools/migrate_to_cms.py --apply")
        return

    WORKS.mkdir(parents=True, exist_ok=True)
    for path, text in writes:
        if path in (PRODUCTS, SITE) and path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        path.write_text(text, encoding="utf-8")
    if PORTFOLIO.exists():
        PORTFOLIO.rename(PORTFOLIO.with_suffix(".yaml.bak"))
    ignore = GITIGNORE.read_text(encoding="utf-8") if GITIGNORE.exists() else ""
    if "*.bak" not in ignore:
        GITIGNORE.write_text(ignore.rstrip("\n") + "\n*.bak\n", encoding="utf-8")
    print("\n변환했습니다. 원래 파일은 .bak 으로 남겨 두었습니다 (GitHub 에는 올라가지 않습니다).")
    print("다음:  python build.py  로 확인한 뒤 git add -A / git commit / git push")


if __name__ == "__main__":
    main()
