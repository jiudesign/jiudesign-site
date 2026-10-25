/**
 * 지우디자인 홈페이지 제작 문의 → 메일 발송 + 구글 시트 기록
 *
 * 설치 방법 (README 의 '제작 문의 메일 설정' 참고)
 *  1) 구글 시트를 새로 만들고  확장 프로그램 → Apps Script
 *  2) 이 파일 내용을 전부 붙여넣고 저장
 *  3) 배포 → 새 배포 → 유형: 웹 앱 / 실행 계정: 나 / 액세스 권한: 모든 사용자 → 배포
 *  4) 나온 웹 앱 URL 을 site.yaml 의 contact.form_endpoint 에 넣기
 *
 * 메일은 이 스크립트를 만든 구글 계정(Gmail)으로 발송되어 아래 주소로 도착합니다.
 */
const TO = 'gracia0304@naver.com';
const SENDER_NAME = '지우디자인 홈페이지';

const FIELDS = [
  ['types', '요청 작업물'],
  ['types_other', '그 밖의 작업'],
  ['ready', '준비된 사항'],
  ['ready_detail', '준비 내용 / 요청 사항'],
  ['due', '받고 싶은 날짜'],
  ['name', '성함 / 상호명'],
  ['phone', '연락처'],
  ['links', '판매처 링크'],
  ['page', '문의한 페이지'],
];

function doPost(e) {
  const p = (e && e.parameter) || {};
  if (p.website) return json_({ ok: true });                 // 스팸(숨김 칸을 채움)
  const clean = {};
  FIELDS.forEach(([k]) => (clean[k] = String(p[k] || '').trim().slice(0, 2000)));
  if (!clean.name || !clean.phone) return json_({ ok: false, error: '성함과 연락처를 입력해 주세요.' });
  if (!clean.types && !clean.types_other) return json_({ ok: false, error: '요청 작업물을 골라주세요.' });

  // 같은 연락처로 1분 안에 여러 번 오면 한 번만 처리
  const cache = CacheService.getScriptCache();
  const key = 'p' + clean.phone.replace(/[^0-9]/g, '');
  if (cache.get(key)) return json_({ ok: true });
  cache.put(key, '1', 60);

  const body = FIELDS
    .filter(([k]) => clean[k])
    .map(([k, label]) => `■ ${label}\n${clean[k]}`)
    .join('\n\n');
  const subject = `[제작 문의] ${clean.name} — ${clean.types || clean.types_other}`;

  MailApp.sendEmail({ to: TO, subject: subject, body: body, name: SENDER_NAME });

  // 구글 시트에서 만든 스크립트라면 문의 내역을 한 줄씩 기록
  try {
    const book = SpreadsheetApp.getActiveSpreadsheet();
    if (book) {
      const sheet = book.getSheets()[0];
      if (sheet.getLastRow() === 0) sheet.appendRow(['접수 시각'].concat(FIELDS.map(([, l]) => l)));
      sheet.appendRow([new Date()].concat(FIELDS.map(([k]) => clean[k])));
    }
  } catch (err) {
    console.error(err);
  }
  return json_({ ok: true });
}

// 브라우저로 웹 앱 URL 을 열었을 때 연결 확인용
function doGet() {
  return json_({ ok: true, message: '제작 문의 접수 주소가 정상입니다.' });
}

// 설치 후 편집기에서 한 번 실행하면 권한 승인 + 시험 메일 발송
function testSend() {
  doPost({ parameter: { types: '상세페이지', name: '시험 발송', phone: '010-0000-0000', ready_detail: '설정이 잘 되었는지 확인하는 메일입니다.' } });
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
