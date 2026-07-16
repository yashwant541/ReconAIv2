(function () {
  'use strict';
  const tokenKey = 'financereconai-session';
  let token = sessionStorage.getItem(tokenKey);
  if (!token) { token = crypto.randomUUID(); sessionStorage.setItem(tokenKey, token); }
  const backend = '/'; // Standard-webapp backend routes are resolved relative to this webapp.
  const headers = () => ({ 'X-FinanceRecon-Session': token });
  const message = (text, error) => { const node = document.querySelector('#message'); node.textContent = text; node.className = error ? 'error' : ''; };
  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  function render(id, records) {
    const root = document.querySelector(id);
    if (!records.length) { root.innerHTML = '<em>No records</em>'; return; }
    const keys = Object.keys(records[0]);
    root.innerHTML = '<table><thead><tr>' + keys.map(k => `<th>${escapeHtml(k)}</th>`).join('') + '</tr></thead><tbody>' + records.map(r => '<tr>' + keys.map(k => `<td>${escapeHtml(r[k])}</td>`).join('') + '</tr>').join('') + '</tbody></table>';
  }
  async function request(url, options = {}) {
    const response = await fetch(backend + url.replace(/^\//, ''), { ...options, headers: { ...headers(), ...(options.headers || {}) }, credentials: 'same-origin' });
    const payload = response.headers.get('content-type')?.includes('application/json') ? await response.json() : null;
    if (!response.ok) throw new Error(payload?.error || 'Request failed');
    return [response, payload];
  }
  async function refresh() { const [, data] = await request('/state'); render('#left-table', data.left); render('#right-table', data.right); render('#result-table', data.results); }
  async function upload(side, input) {
    if (!input.files.length) return;
    const data = new FormData(); [...input.files].forEach(file => data.append('files', file));
    message(`Uploading ${input.files.length} ${side} file(s)…`);
    try { const [, out] = await request(`/upload/${side}`, { method: 'POST', body: data }); message(`Extracted ${out.count} ${side} records.`); await refresh(); }
    catch (e) { message(e.message, true); } finally { input.value = ''; }
  }
  document.querySelector('#left-files').addEventListener('change', e => upload('left', e.target));
  document.querySelector('#right-files').addEventListener('change', e => upload('right', e.target));
  document.querySelector('#reconcile').addEventListener('click', async () => { try { const [, out] = await request('/reconcile', { method: 'POST' }); render('#result-table', out.results); message(`Reconciled ${out.results.length} rows.`); } catch(e) { message(e.message, true); } });
  document.querySelector('#clear').addEventListener('click', async () => { await request('/clear', { method: 'POST' }); token = crypto.randomUUID(); sessionStorage.setItem(tokenKey, token); await refresh(); message('Session data cleared.'); });
  document.querySelector('#export').addEventListener('click', async () => {
    try {
      const fmt = document.querySelector('#format').value;
      const response = await fetch(backend + `export/${fmt}`, { headers: headers(), credentials: 'same-origin' });
      if (!response.ok) throw new Error('Export failed');
      const url = URL.createObjectURL(await response.blob());
      const link = Object.assign(document.createElement('a'), { href: url, download: `reconciliation.${fmt}` });
      link.click(); URL.revokeObjectURL(url);
    } catch (e) { message(e.message, true); }
  });
  refresh().catch(e => message(e.message, true));
}());
