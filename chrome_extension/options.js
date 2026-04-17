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

chrome.storage.sync.get(DEFAULTS, (o) => {
  document.getElementById('apiBase').value = o.apiBase || '';
  document.getElementById('appUrl').value = o.appUrl || '';
  document.getElementById('resumeDocId').value = o.resumeDocId || '';
});

document.getElementById('save').addEventListener('click', () => {
  const apiBase = document.getElementById('apiBase').value.trim() || DEFAULTS.apiBase;
  const appUrl = document.getElementById('appUrl').value.trim() || DEFAULTS.appUrl;
  const resumeDocId = extractDocId(document.getElementById('resumeDocId').value);
  chrome.storage.sync.set({ apiBase, appUrl, resumeDocId }, () => {
    document.getElementById('save').textContent = 'Saved';
    setTimeout(() => { document.getElementById('save').textContent = 'Save'; }, 1500);
  });
});
