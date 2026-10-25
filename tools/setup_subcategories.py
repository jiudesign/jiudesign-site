#!/usr/bin/env python3
"""
상세페이지 디자인 세부 분류 설정 도구

    python tools/setup_subcategories.py          # 미리보기: 무엇이 바뀔지 표로 보여주기만 함
    python tools/setup_subcategories.py --apply  # 실제로 파일 수정 (수정 전 .bak 백업 생성)

하는 일
  1) content/site.yaml
     - 상세페이지 디자인에 세부 분류 7개를 추가 (이미 있으면 그대로 둠)
     - 대분류 순서를 상세페이지 → 브로슈어 → 포스터 → 패키지 → AI 로 정리
       (그 밖의 분류는 그 뒤에 그대로 둠)
  2) content/portfolio.yaml
     - 상세페이지 작업물마다 제목·slug 의 단어로 세부 분류를 추정해 `subcategory:` 줄을 넣음
     - 이미 subcategory 가 있는 작업물은 건드리지 않음
     - 추정하지 못한 작업물은 표에 "?" 로 표시 → 직접 넣어주세요

주석·줄 순서는 그대로 두고 필요한 줄만 넣습니다.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "content" / "site.yaml"
PORTFOLIO = ROOT / "content" / "portfolio.yaml"

SUBCATEGORIES = [
    ("food", "식품·건강기능식품"),
    ("living", "가구·침구·생활용품"),
    ("hobby-pet", "취미·반려동물"),
    ("beauty-medical", "화장품·목욕·미용·의료"),
    ("service", "설치·교육·서비스"),
    ("electronics", "컴퓨터·가전·휴대폰"),
    ("fashion", "의류·소품·신발"),
]
TOP_ORDER = ["detail-page", "brochure", "poster", "package", "ai"]

# 제목(한글)에 들어 있으면 해당 분류로 봄. 위에 있는 분류가 먼저 적용됩니다.
KO = {
    "electronics": ["냉동고", "냉장고", "쇼케이스", "가전", "컴퓨터", "노트북", "휴대폰", "스마트폰", "충전기",
                    "이어폰", "LED", "조명", "전자", "모니터", "선풍기", "청소기"],
    "beauty-medical": ["마스크", "화장품", "크림", "세럼", "앰플", "샴푸", "바디", "목욕", "미용", "뷰티",
                       "의료", "메디", "피부", "치약", "칫솔", "향수", "네일"],
    "hobby-pet": ["장난감", "놀이", "완구", "요가", "운동", "헬스", "반려", "고양이", "강아지", "펫", "취미",
                  "낚시", "캠핑", "골프", "자전거"],
    "service": ["변호사", "법률", "설치", "교육", "학원", "강의", "서비스", "상담", "컨설팅", "병원", "시공",
                "인테리어 시공", "경사로"],
    "living": ["가구", "침구", "이불", "베개", "매트리스", "데스크", "책상", "테이블", "의자", "생활", "머그",
               "컵", "그릇", "식기", "주방", "수납", "분무기", "공구", "비트", "매트", "커튼", "라면기"],
    "fashion": ["신발", "슈즈", "운동화", "의류", "모자", "가방", "지갑", "소품", "구매대행", "옷", "티셔츠",
                "주얼리", "액세서리", "양말"],
    "food": ["식품", "두부", "국수", "파스타", "베리", "마카롱", "천혜향", "한라봉", "레드향", "칡즙", "즙", "떡", "도라지",
             "커피", "원두", "발효", "복숭아", "호박", "홍삼", "인삼", "삼계탕", "토마토", "건강기능", "프로틴",
             "류신", "프로폴리스", "꿀", "과일", "사과", "딸기", "녹차", "홍차", "쌀", "김치", "소스", "간식", "빵",
             "케이크", "고기", "한우", "음료", "주스", "영양제", "비타민"],
}
# slug/파일명(영문)을 _ - 로 나눈 단어와 비교. 5글자 이상이면 단어 안에 포함돼도 인정
EN = {
    "electronics": ["freezer", "led", "light", "lamp", "phone", "computer", "laptop", "charger"],
    "beauty-medical": ["mask", "medicare", "medi", "cosmetic", "beauty", "skin", "shampoo", "cream"],
    "hobby-pet": ["toy", "kids-kitchen", "yoga", "pet", "miaowang", "cat", "dog", "camping", "golf"],
    "service": ["law", "office", "academy", "service", "ramp"],
    "living": ["bedding", "desk", "table", "household", "mug", "bowl", "ramenbowl", "coil", "kingscoil",
               "furniture", "mat", "sprayer", "bit"],
    "fashion": ["shoe", "shoes", "shoer", "hat", "kidshat", "bag", "wallet", "gumae", "fashion"],
    "food": ["tofu", "noodle", "berry", "macaron", "cheonhyehyang", "chikjeup", "tteok", "creamtteok",
             "doraji", "ethiopia", "coffee", "ferment", "freshmorning", "peach", "pumkin", "pumpkin",
             "redginseng", "ginseng", "redhyang", "samgyetang", "tomato", "leucine", "propolis",
             "kingsberry", "honey", "food"],
}


def guess(slug: str, title: str) -> str | None:
    for sub, words in KO.items():
        if any(w in title for w in words):
            return sub
    tokens = [t for t in re.split(r"[-_ .]+", slug.lower()) if t]
    for sub, words in EN.items():
        for w in words:
            if "-" in w and w in slug.lower():
                return sub
            if any(t == w or (len(w) >= 5 and w in t) for t in tokens):
                return sub
    return None


# ─────────────────────────────────────────────
def patch_site(text: str) -> tuple[str, list[str]]:
    notes = []
    data = yaml.safe_load(text)
    cats = {c["slug"]: c for c in data["categories"]}
    if "detail-page" not in cats:
        sys.exit("site.yaml 에 detail-page 분류가 없습니다.")

    # 분류 블록(  - slug: xxx 부터 다음 '  - slug:' 직전까지)을 잘라 순서 정리
    m = re.search(r"(?ms)^categories:\n(.*?)(?=^\S)", text)
    if not m:
        sys.exit("site.yaml 에서 categories: 블록을 찾지 못했습니다.")
    body = m.group(1)
    parts = re.split(r"(?m)^(?=  - slug: )", body)
    head = parts[0] if not parts[0].startswith("  - slug: ") else ""
    blocks = [p for p in parts if p.startswith("  - slug: ")]
    by_slug = {re.match(r"  - slug: (\S+)", b).group(1): b for b in blocks}

    db = by_slug["detail-page"]
    if "subcategories:" not in db:
        sub_text = "    subcategories:\n" + "".join(
            f"      - slug: {s}\n        name: {n}\n" for s, n in SUBCATEGORIES)
        db = db.rstrip("\n") + "\n" + sub_text
        if not by_slug["detail-page"].endswith("\n\n") and not db.endswith("\n"):
            db += "\n"
        by_slug["detail-page"] = db
        notes.append("상세페이지 디자인에 세부 분류 7개 추가")
    else:
        notes.append("상세페이지 디자인 세부 분류: 이미 있음 (그대로 둠)")

    order = [s for s in TOP_ORDER if s in by_slug] + [s for s in by_slug if s not in TOP_ORDER]
    old_order = [re.match(r"  - slug: (\S+)", b).group(1) for b in blocks]
    if order != old_order:
        notes.append("대분류 순서 정리: " + " → ".join(cats[s]["name"] for s in order))
    new_body = head + "".join(by_slug[s].rstrip("\n") + "\n" for s in order) + "\n"
    return text[:m.start(1)] + new_body + text[m.end(1):], notes


def patch_portfolio(text: str) -> tuple[str, list[tuple[str, str, str]]]:
    items = yaml.safe_load(text) or []
    rows = []
    for it in items:
        if it.get("category") != "detail-page":
            continue
        slug, title = str(it["slug"]), str(it.get("title", ""))
        if it.get("subcategory"):
            rows.append((slug, title, f"{it['subcategory']} (기존)"))
            continue
        g = guess(slug, title) or guess(Path(str(it.get("image", ""))).stem, title)
        rows.append((slug, title, g or "?"))
        if not g:
            continue
        pat = re.compile(rf"(?m)^(- slug: {re.escape(slug)}[ \t]*\n(?:[ \t]+.*\n)*?)([ \t]+)(category: detail-page[ \t]*\n)")
        text, n = pat.subn(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(2)}subcategory: {g}\n", text, count=1)
        if not n:
            rows[-1] = (slug, title, f"{g} (자동 입력 실패 — 직접 넣어주세요)")
    return text, rows


def main() -> None:
    ap = argparse.ArgumentParser(description="상세페이지 세부 분류 설정")
    ap.add_argument("--apply", action="store_true", help="실제로 파일을 수정 (.bak 백업 생성)")
    args = ap.parse_args()

    site_text = SITE.read_text(encoding="utf-8")
    port_text = PORTFOLIO.read_text(encoding="utf-8")
    new_site, notes = patch_site(site_text)
    new_port, rows = patch_portfolio(port_text)

    print("site.yaml")
    for n in notes:
        print("  -", n)
    names = dict(SUBCATEGORIES)
    print(f"\nportfolio.yaml — 상세페이지 작업물 {len(rows)}개")
    unknown = 0
    for slug, title, sub in rows:
        label = names.get(sub.split(" ")[0], sub)
        if sub == "?":
            unknown += 1
            label = "?  ← 직접 지정 필요"
        print(f"  {slug[:28]:<28}  {label:<22}  {title[:34]}")

    # 결과가 올바른 YAML 인지 확인
    try:
        yaml.safe_load(new_site), yaml.safe_load(new_port)
    except yaml.YAMLError as e:
        sys.exit(f"\n[중단] 수정 결과가 올바른 형식이 아닙니다. 파일은 바꾸지 않았습니다.\n{e}")

    if not args.apply:
        print("\n미리보기입니다. 위 내용대로 바꾸려면:  python tools/setup_subcategories.py --apply")
        return
    shutil.copy2(SITE, SITE.with_suffix(".yaml.bak"))
    shutil.copy2(PORTFOLIO, PORTFOLIO.with_suffix(".yaml.bak"))
    SITE.write_text(new_site, encoding="utf-8")
    PORTFOLIO.write_text(new_port, encoding="utf-8")
    print("\n적용했습니다. 원래 파일은 site.yaml.bak, portfolio.yaml.bak 으로 저장해 두었습니다.")
    if unknown:
        print(f"'?' 로 표시된 {unknown}개는 portfolio.yaml 에서 category 아래 줄에 직접 넣어주세요. 예)")
        print("  category: detail-page\n  subcategory: food")
    print("쓸 수 있는 값: " + ", ".join(f"{s}({n})" for s, n in SUBCATEGORIES))


if __name__ == "__main__":
    main()
