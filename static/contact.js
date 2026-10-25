// 제작 문의 폼 — Google Apps Script 주소(site.yaml contact.form_endpoint)로 보내면
// 대표님 구글 계정에서 메일이 발송됩니다.
(() => {
  const form = document.getElementById("inquiry");
  if (!form) return;
  const endpoint = form.dataset.endpoint;
  const msg = form.querySelector(".form__msg");
  const btn = form.querySelector(".form__submit");

  const say = (text, kind) => {
    msg.textContent = text;
    msg.dataset.kind = kind || "";
  };

  // /contact/?type=상세페이지 로 들어오면 해당 작업을 미리 체크
  const pre = new URLSearchParams(location.search).get("type");
  if (pre) {
    form.querySelectorAll('input[name="types"]').forEach((c) => {
      if (c.value.includes(pre) || pre.includes(c.value)) c.checked = true;
    });
  }

  if (!endpoint) {
    btn.disabled = true;
    say("온라인 문의는 준비 중입니다. 지금은 위 전화번호로 연락 주세요.", "info");
    return;
  }

  const all = (name) => [...form.querySelectorAll(`input[name="${name}"]:checked`)].map((c) => c.value);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = form.elements;
    const types = all("types");
    const firstBad = [];
    if (!types.length && !f.types_other.value.trim()) firstBad.push([form.querySelector('input[name="types"]'), "요청 작업물을 하나 이상 골라주세요."]);
    if (!f.name.value.trim()) firstBad.push([f.name, "성함 또는 상호명을 적어주세요."]);
    if (!/[0-9]{7,}/.test(f.phone.value.replace(/[^0-9]/g, ""))) firstBad.push([f.phone, "연락 가능한 전화번호를 적어주세요."]);
    if (!f.agree.checked) firstBad.push([f.agree, "개인정보 수집·이용에 동의해 주세요."]);
    if (firstBad.length) {
      const [el, text] = firstBad[0];
      say(text, "error");
      el.focus();
      return;
    }

    const body = new URLSearchParams({
      types: types.join(", "),
      types_other: f.types_other.value.trim(),
      ready: all("ready").join(", "),
      ready_detail: f.ready_detail.value.trim(),
      due: [f.due.value, all("due_flexible").join("")].filter(Boolean).join(" / "),
      name: f.name.value.trim(),
      phone: f.phone.value.trim(),
      links: f.links.value.trim(),
      website: f.website.value,
      page: location.href,
    });

    btn.disabled = true;
    btn.textContent = "보내는 중…";
    say("", "");
    let ok = false;
    try {
      const res = await fetch(endpoint, { method: "POST", body });
      const data = await res.json().catch(() => ({}));
      ok = res.ok && data.ok !== false;
      if (!ok && data.error) say(data.error, "error");
    } catch (err) {
      // 일부 브라우저는 응답을 읽지 못하게 막음 → 전송만 하고 접수로 안내
      try {
        await fetch(endpoint, { method: "POST", body, mode: "no-cors" });
        ok = true;
      } catch (_) { ok = false; }
    }
    btn.disabled = false;
    btn.textContent = "문의 보내기";
    if (ok) {
      form.reset();
      say("문의가 접수되었습니다. 확인 후 연락드리겠습니다. 하루가 지나도 연락이 없으면 전화 주세요.", "ok");
    } else if (!msg.textContent) {
      say("보내지 못했습니다. 잠시 후 다시 시도하시거나 위 전화번호로 연락 주세요.", "error");
    }
    msg.scrollIntoView({ block: "center", behavior: "smooth" });
  });
})();
