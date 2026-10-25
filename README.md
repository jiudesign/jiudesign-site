# JIUDESIGN 포트폴리오 사이트

Creatorlink(jiudesigner.creatorlink.net)를 대신하는 무료 포트폴리오 사이트입니다.
Python으로 정적 HTML을 만들어 **Cloudflare Pages 무료 플랜**에 올립니다. 호스팅 비용은 0원입니다.

- 상세페이지는 실제 쇼핑몰과 같은 **가로 860px**으로, 처음부터 끝까지 스크롤해서 보여줍니다.
- 세로로 긴 원본 이미지는 자동으로 **WebP 변환 + 3,000px 단위 분할** → 빠르게 열리고 파일당 25MiB 제한에 걸리지 않습니다.
- 새 작업물 추가 = 이미지 1장 넣고 `portfolio.yaml`에 몇 줄 추가.

---

## 1. 처음 한 번: 내 PC에서 실행해 보기

Python 3.10 이상이 필요합니다.

```powershell
cd jiudesign-site
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# Creatorlink 에 올려둔 원본 이미지 16장 내려받기
python tools/import_creatorlink.py --download

# 사이트 생성 + 미리보기
python build.py --serve
```

브라우저에서 http://localhost:8000 을 열면 됩니다.

> 이미지를 아직 내려받지 않았어도 빌드는 됩니다. 이 경우 Creatorlink 원본 이미지를 임시로 연결해 보여주고 경고를 출력합니다.
> 배포 전에는 반드시 `--download`로 이미지를 받아두세요 (Creatorlink를 해지하면 원본 링크가 끊길 수 있습니다).

### Creatorlink에만 있고 여기 없는 작업물 가져오기

현재 목록은 메인 화면에 보이는 16개 기준입니다. 브로슈어·포스터·패키지·AI 디자인 분류의 작업물은 아래 명령으로 찾습니다.

```powershell
python tools/import_creatorlink.py --scan
```

`content/portfolio.new.yaml`에 초안이 생깁니다. slug·분류를 확인한 뒤 `portfolio.yaml`로 옮겨 붙이고 `--download`를 다시 실행하세요.

---

## 2. 무료 배포 (Cloudflare Pages)

### 준비물
- GitHub 계정 (무료)
- Cloudflare 계정 (무료, 카드 등록 불필요)

### 방법 A — 가장 간단: Cloudflare가 GitHub 저장소를 직접 빌드

1. 이 폴더를 GitHub 저장소로 올립니다. (`content/images/` 원본 이미지 포함)
2. Cloudflare 대시보드 → **Workers & Pages → Create → Pages → Connect to Git** → 저장소 선택
3. 빌드 설정
   | 항목 | 값 |
   |---|---|
   | Framework preset | None |
   | Build command | `pip install -r requirements.txt && python build.py --strict` |
   | Build output directory | `dist` |
   | 환경 변수 | `PYTHON_VERSION` = `3.12` |
4. **Save and Deploy** → 1~2분 뒤 `https://<프로젝트명>.pages.dev` 주소가 생깁니다.

이후로는 GitHub에 push할 때마다 자동으로 다시 배포됩니다.

### 방법 B — 이미지가 많아졌을 때: GitHub Actions로 빌드

방법 A는 매번 모든 이미지를 새로 변환합니다. 작업물이 수십~수백 개로 늘면 `.github/workflows/deploy.yml`을 쓰세요. 변환한 이미지를 캐시해서 **바뀐 이미지만** 다시 처리합니다.

1. Cloudflare에서 Pages 프로젝트를 `jiudesign` 이름으로 만들되 **Direct Upload**로 생성
2. Cloudflare → My Profile → API Tokens → 템플릿 없이 생성, 권한 `Account / Cloudflare Pages / Edit`
3. GitHub 저장소 → Settings → Secrets and variables → Actions 에 등록
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID` (Cloudflare 대시보드 오른쪽 사이드바)
4. main 브랜치에 push → Actions 탭에서 배포 확인

방법 A와 B 중 하나만 쓰세요. B를 쓸 때는 Pages 프로젝트에 Git 연결을 하지 않습니다.

### 내 도메인 연결 (선택)

도메인(예: jiudesign.kr)을 구입했다면 Pages 프로젝트 → **Custom domains → Set up a domain**.
연결 후 `content/site.yaml`의 `base_url`을 새 주소로 바꿔주세요 (sitemap·공유 미리보기에 쓰입니다).

---

## 3. 평소 운영

### 새 작업물 추가
1. 원본 이미지를 `content/images/work/`에 넣습니다 (jpg/png/webp, 세로로 긴 상세페이지 그대로).
2. `content/portfolio.yaml` 맨 위에 추가합니다.

```yaml
- slug: honey-gift-set          # 주소: /work/detail-page/honey-gift-set/
  title: 꿀 선물세트 상세페이지
  category: detail-page         # site.yaml 의 categories.slug 중 하나
  concept: “한 숟가락의 정성”, 명절 선물용 프리미엄 꿀
  tone: 골드·아이보리, 명조 타이틀
  structure: [메인 비주얼, 원산지, 선물 포장, 구성 안내]
  image: work/honey-gift-set.jpg
```

3. `python build.py --serve`로 확인 → GitHub에 push.

`featured: true`를 붙인 작업물이 메인 화면 오른쪽 뷰어에서 흘러갑니다.

### 디자인 노트 쓰기
`content/notes/2026-11-01-제목.md` 형식으로 파일을 만듭니다. 파일 맨 위에 아래를 넣고 본문은 마크다운으로 씁니다.

```markdown
---
title: 상세페이지 첫 화면에서 꼭 보여줘야 할 3가지
date: 2026-11-01
summary: 고객이 3초 안에 이탈하지 않게 만드는 방법
---
본문...
```

### 디자인 상품 수정
`content/products.yaml`. 가격은 숫자 대신 `"30만원부터"`, `문의` 같은 글로 넣어도 됩니다.

### 연락처·카카오톡
`content/site.yaml`의 `contact`. `kakao`에 카카오톡 채널/오픈채팅 주소를 넣으면 버튼이 생깁니다.

---

## 4. 무료 한도와 이 사이트의 대응

| Cloudflare Pages 무료 한도 | 이 사이트에서는 |
|---|---|
| 대역폭·요청 수 | 무제한 |
| 사이트당 파일 20,000개 | 작업물 1개 ≈ 파일 3~8개 → 수천 개까지 여유 |
| 파일 1개 25MiB | 긴 이미지는 3,000px 단위로 자르고 WebP로 변환, 빌드 시 자동 검사 |
| 빌드 월 500회 | push 1회 = 빌드 1회 |

빌드가 끝나면 배포 크기와 파일 수가 출력됩니다. 한도를 넘으면 빌드가 실패하고 이유를 알려줍니다.

---

## 폴더 구조

```
content/
  site.yaml          사이트 이름, 문구, 연락처, 분류, 이미지 설정
  portfolio.yaml     작업물 목록
  products.yaml      디자인 상품
  notes/*.md         디자인 노트
  images/work/       작업물 원본 이미지
templates/           HTML 템플릿 (Jinja2)
static/              CSS, JS, static/root/ 는 사이트 루트로 복사 (favicon 등)
tools/
  import_creatorlink.py   Creatorlink 이전 도구
build.py             빌더 → dist/
```

## build.py 옵션

| 명령 | 설명 |
|---|---|
| `python build.py` | dist/ 생성 |
| `python build.py --serve` | 생성 후 http://localhost:8000 미리보기 |
| `python build.py --strict` | 로컬 이미지가 하나라도 없으면 실패 (배포용) |
| `python build.py --clean` | 이미지 캐시를 지우고 전부 다시 변환 (site.yaml 이미지 설정을 바꿨을 때는 자동으로 다시 변환되므로 보통 불필요) |
