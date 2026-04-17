const DEFAULTS = { apiBase: 'http://localhost:8000', resumeDocId: '', appUrl: 'http://localhost:3000' };

function extractDocId(v) {
  if (!v) return '';
  const s = String(v).trim();
  if (!s) return '';
  const m = s.match(/\/d\/([a-zA-Z0-9_-]{10,})/);
  if (m && m[1]) return m[1];
  const m2 = s.match(/[?&]id=([a-zA-Z0-9_-]{10,})/);
  if (m2 && m2[1]) return m2[1];
  return s;
}

async function getOptions() {
  const o = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...o };
}

function setStatus(msg, isError = false) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'status' + (isError ? ' error' : '');
}

function showResult(data) {
  const el = document.getElementById('result');
  el.classList.remove('hidden');
  el.innerHTML = '';
  if (data.error) {
    el.innerHTML = `<p class="error">${escapeHtml(data.error)}</p>`;
    return;
  }
  const score = data.score != null ? data.score : '—';
  const shouldApply = data.should_apply ? 'Yes' : 'No';
  let html = `<h3>Fit score: <span class="score">${score}/10</span></h3><p><strong>Should apply:</strong> ${shouldApply}</p>`;
  if (data.recommendations && data.recommendations.length) {
    html += '<p><strong>Recommendations:</strong></p><ul>';
    data.recommendations.slice(0, 5).forEach(r => { html += `<li>${escapeHtml(r)}</li>`; });
    html += '</ul>';
  }
  if (data.matching_areas && data.matching_areas.length) {
    html += '<p><strong>Matching:</strong></p><ul>';
    data.matching_areas.slice(0, 3).forEach(m => { html += `<li>${escapeHtml(m)}</li>`; });
    html += '</ul>';
  }
  if (data.missing_areas && data.missing_areas.length) {
    html += '<p><strong>Missing:</strong></p><ul>';
    data.missing_areas.slice(0, 3).forEach(m => { html += `<li>${escapeHtml(m)}</li>`; });
    html += '</ul>';
  }
  el.innerHTML = html;
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

async function getCurrentTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.url || '';
}

/** Try to extract job title and company from the current page (runs in tab via executeScript) */
function extractJobInfoFromPage() {
  let jobTitle = '';
  let company = '';
  // og:title often "Job Title at Company" or "Job Title - Company | Site"
  const ogTitle = document.querySelector('meta[property="og:title"]');
  if (ogTitle && ogTitle.content) {
    const t = ogTitle.content.trim();
    const atIdx = t.toLowerCase().indexOf(' at ');
    const dashIdx = t.indexOf(' - ');
    const pipeIdx = t.indexOf(' | ');
    if (atIdx > 0) {
      jobTitle = t.slice(0, atIdx).trim();
      company = t.slice(atIdx + 4, pipeIdx > 0 ? pipeIdx : t.length).trim();
    } else if (dashIdx > 0) {
      jobTitle = t.slice(0, dashIdx).trim();
      company = t.slice(dashIdx + 3, pipeIdx > 0 ? pipeIdx : t.length).trim();
    } else if (pipeIdx > 0) {
      jobTitle = t.slice(0, pipeIdx).trim();
    } else {
      jobTitle = t;
    }
  }
  // JSON-LD JobPosting
  if ((!jobTitle || !company) && document.body) {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const s of scripts) {
      try {
        const data = typeof s.textContent === 'string' ? JSON.parse(s.textContent) : null;
        const obj = Array.isArray(data) ? data.find((x) => x['@type'] === 'JobPosting') : data && data['@type'] === 'JobPosting' ? data : null;
        if (obj) {
          if (!jobTitle && obj.title) jobTitle = obj.title;
          if (!company && obj.hiringOrganization) {
            company = typeof obj.hiringOrganization === 'string' ? obj.hiringOrganization : (obj.hiringOrganization.name || '');
          }
          break;
        }
      } catch (_) {}
    }
  }
  // Common headings (LinkedIn, Indeed, etc.)
  if (!jobTitle) {
    const h1 = document.querySelector('h1');
    if (h1 && h1.innerText) jobTitle = h1.innerText.trim();
  }
  if (!company) {
    const sel = document.querySelector('[data-tracking-control-name="public_jobs_topcard-org-name"], .job-details-jobs-unified-top-card__company-name, .jobsearch-CompanyInfoContainer .icl-u-lg-mr--sm, [data-company-name]');
    if (sel && sel.innerText) company = sel.innerText.trim();
  }
  return { jobTitle: jobTitle.slice(0, 200), company: company.slice(0, 200) };
}

async function tryPrefillFromPage() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url || !tab.url.startsWith('http')) return;
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractJobInfoFromPage,
    });
    const r = results?.[0]?.result;
    if (r && (r.jobTitle || r.company)) {
      if (r.jobTitle) document.getElementById('inputJobTitle').value = r.jobTitle;
      if (r.company) document.getElementById('inputCompany').value = r.company;
    }
  } catch (_) {}
}

async function checkAuth() {
  const opts = await getOptions();
  const statusEl = document.getElementById('authStatus');
  const btnSignIn = document.getElementById('btnSignIn');
  try {
    const r = await fetch(`${opts.apiBase}/api/auth/google/status`, { credentials: 'include' });
    const data = await r.json().catch(() => ({}));
    if (data.authenticated && data.user && data.user.email) {
      statusEl.textContent = `Signed in as ${data.user.email}`;
      statusEl.classList.add('signed-in');
      btnSignIn.classList.add('hidden');
    } else {
      statusEl.textContent = 'Sign in to evaluate fit and tailor.';
      statusEl.classList.remove('signed-in');
      btnSignIn.classList.remove('hidden');
    }
  } catch {
    statusEl.textContent = 'Cannot reach API. Start the backend (make api).';
    btnSignIn.classList.remove('hidden');
  }
}

document.getElementById('pageUrl').textContent = 'Loading…';
getCurrentTabUrl().then(url => {
  document.getElementById('pageUrl').textContent = url || 'No tab URL';
});
tryPrefillFromPage();
checkAuth();

document.getElementById('btnSignIn').addEventListener('click', async () => {
  const opts = await getOptions();
  chrome.tabs.create({ url: `${opts.apiBase}/api/auth/google/login` });
  document.getElementById('authStatus').textContent = 'Sign-in opened in new tab. Complete the flow, then close the popup and try again.';
});

document.getElementById('btnEvaluate').addEventListener('click', async () => {
  const url = await getCurrentTabUrl();
  if (!url || !url.startsWith('http')) {
    setStatus('Open a job listing page first.', true);
    return;
  }
  const opts = await getOptions();
  const btn = document.getElementById('btnEvaluate');
  btn.disabled = true;
  setStatus('Evaluating fit…');
  document.getElementById('result').classList.add('hidden');
  try {
    const body = { job_url: url };
    if (opts.resumeDocId) body.resume_doc_id = extractDocId(opts.resumeDocId);
    const r = await fetch(`${opts.apiBase}/api/evaluate-fit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'include',  // send session cookie (from "Sign in with Google" in web app)
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (r.status === 401) {
        checkAuth();  // refresh auth section so "Sign in with Google" shows
        setStatus('Session expired. Sign in again above.');
      } else {
        setStatus('', true);
      }
      showResult({ error: data.detail || data.message || `Error ${r.status}` });
    } else {
      showResult(data);
      setStatus('Done.');
    }
  } catch (e) {
    setStatus('Request failed. Is the API running?', true);
    showResult({ error: e.message });
  }
  btn.disabled = false;
});

function buildAppUrlWithParams(baseUrl, jobUrl, company, jobTitle) {
  const params = new URLSearchParams();
  if (jobUrl) params.set('job_url', jobUrl);
  if (company && company.trim()) params.set('company', company.trim());
  if (jobTitle && jobTitle.trim()) params.set('job_title', jobTitle.trim());
  const q = params.toString();
  return q ? `${baseUrl}?${q}` : baseUrl;
}

document.getElementById('btnTailor').addEventListener('click', async () => {
  const url = await getCurrentTabUrl();
  if (!url || !url.startsWith('http')) {
    setStatus('Open a job listing page first.', true);
    return;
  }
  const opts = await getOptions();
  const appUrl = opts.appUrl || 'http://localhost:3000';
  const company = document.getElementById('inputCompany').value.trim();
  const jobTitle = document.getElementById('inputJobTitle').value.trim();
  const openUrl = buildAppUrlWithParams(appUrl, url, company, jobTitle);
  chrome.tabs.create({ url: openUrl });
  setStatus('Opened app. Complete tailoring there.');
});

document.getElementById('linkOpenApp').addEventListener('click', async (e) => {
  e.preventDefault();
  const opts = await getOptions();
  const appUrl = opts.appUrl || 'http://localhost:3000';
  const url = await getCurrentTabUrl();
  const company = document.getElementById('inputCompany').value.trim();
  const jobTitle = document.getElementById('inputJobTitle').value.trim();
  const openUrl = buildAppUrlWithParams(appUrl, url || '', company, jobTitle);
  chrome.tabs.create({ url: openUrl });
});

document.getElementById('linkOptions').addEventListener('click', (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});
