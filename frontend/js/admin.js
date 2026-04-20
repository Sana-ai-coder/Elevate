const ADMIN_SESSION_KEY = 'elevate_user_session';
const SCHOOL_SLUG_HINT_KEY = 'elevate_school_slug_hint';
const SCHOOL_SLUG_HINT_COOKIE = 'elevate_school_slug_hint';

function loadAuthSession() {
  try {
    const fromLocal = localStorage.getItem(ADMIN_SESSION_KEY);
    const fromSession = sessionStorage.getItem(ADMIN_SESSION_KEY);
    const raw = fromLocal || fromSession;
    return raw ? JSON.parse(raw) : null;
  } catch (error) {
    console.warn('Failed to parse auth session for admin page:', error);
    localStorage.removeItem(ADMIN_SESSION_KEY);
    sessionStorage.removeItem(ADMIN_SESSION_KEY);
    return null;
  }
}

function roleHomePage(role) {
  const normalized = String(role || 'student').trim().toLowerCase();
  const page = normalized === 'teacher'
    ? 'teacher-dashboard.html'
    : normalized === 'admin'
      ? 'admin.html'
      : 'dashboard.html';

  const session = loadAuthSession();
  const pathParts = String(window.location.pathname || '').split('/').filter(Boolean);
  const pathSlug = pathParts.length > 0 && !String(pathParts[0]).includes('.') ? String(pathParts[0]).trim().toLowerCase() : '';
  const slug = String(session?.user?.school_slug || pathSlug || '').trim().toLowerCase();
  if (!slug) return page;
  return `/${slug}/${page}`;
}

function enforceAdminSessionGuard() {
  const session = loadAuthSession();
  if (!session || !session.user || !session.token) {
      window.location.replace('/index.html');
    return null;
  }

  const role = String(session.user.role || 'student').trim().toLowerCase();
  if (role !== 'admin') {
    window.location.replace(roleHomePage(role));
    return null;
  }

  return session;
}

function clearAuthSession() {
  localStorage.removeItem(ADMIN_SESSION_KEY);
  sessionStorage.removeItem(ADMIN_SESSION_KEY);
  localStorage.removeItem(SCHOOL_SLUG_HINT_KEY);
  sessionStorage.removeItem(SCHOOL_SLUG_HINT_KEY);
  document.cookie = `${SCHOOL_SLUG_HINT_COOKIE}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; samesite=lax`;
}

function getInitial(name) {
  const trimmed = String(name || '').trim();
  return trimmed ? trimmed.charAt(0).toUpperCase() : 'A';
}

function setupAdminNavbar(session) {
  const user = session?.user || {};
  const info = document.getElementById('adminUserInfo');
  const nameEl = document.getElementById('adminUserName');
  const avatar = document.getElementById('adminUserAvatar');
  const profileMenu = document.getElementById('adminProfileMenu');
  const toggle = document.getElementById('adminProfileMenuToggle');
  const logoutBtn = document.getElementById('adminLogoutBtn');

  if (nameEl) nameEl.textContent = user.name || 'Admin';
  if (avatar) avatar.textContent = getInitial(user.name || 'Admin');
  if (info) info.classList.add('hydrated');

  if (toggle && profileMenu) {
    toggle.addEventListener('click', (evt) => {
      evt.preventDefault();
      evt.stopPropagation();
      const isOpen = profileMenu.classList.toggle('open');
      toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });

    document.addEventListener('click', (evt) => {
      if (!profileMenu.classList.contains('open')) return;
      if (profileMenu.contains(evt.target)) return;
      profileMenu.classList.remove('open');
      toggle.setAttribute('aria-expanded', 'false');
    });
  }

  if (logoutBtn) {
    logoutBtn.addEventListener('click', () => {
      clearAuthSession();
      window.location.replace('/index.html');
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const session = enforceAdminSessionGuard();
  if (!session) return;
  setupAdminNavbar(session);

  function getGradeDisplayName(grade) {
    const gradeNames = {
      elementary: 'Elementary (K-5)',
      middle: 'Middle School (6-8)',
      high: 'High School (9-12)',
      college: 'College'
    };
    return gradeNames[grade] || grade || 'N/A';
  }

  // If the page is served from a different port (e.g. Live Server on 5500),
  // point API calls at the backend dev server running on port 5000.
  const API_BASE = (location.port && location.port !== '5000') ? `http://${location.hostname}:5000` : '';
  function apiFetch(path, opts) { return fetch(API_BASE + path, opts); }
  // Admin stats
  const getStatsBtn = document.getElementById('getStats');
  const statsOut = document.getElementById('statsOutput');
  const adminTokenInput = document.getElementById('adminToken');
  const adminTokenRequestsInput = document.getElementById('adminTokenRequests');
  adminTokenInput.value = adminTokenInput.placeholder || 'dev-admin-token';
  if (adminTokenRequestsInput) {
    adminTokenRequestsInput.value = adminTokenInput.value;
    adminTokenInput.addEventListener('input', () => { adminTokenRequestsInput.value = adminTokenInput.value; });
    adminTokenRequestsInput.addEventListener('input', () => { adminTokenInput.value = adminTokenRequestsInput.value; });
  }
  function getAdminToken(){
    return (adminTokenRequestsInput && adminTokenRequestsInput.value.trim()) || (adminTokenInput && adminTokenInput.value.trim()) || '';
  }

  getStatsBtn.addEventListener('click', async () => {
    const token = adminTokenInput.value.trim();
    statsOut.textContent = 'Loading...';
    try {
      const res = await apiFetch('/api/admin/stats', {
        headers: { 'X-Admin-Token': token }
      });
      if (!res.ok) {
        const text = await res.text();
        statsOut.textContent = `Error ${res.status}: ${text}`;
        return;
      }
      const data = await res.json();
      statsOut.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      statsOut.textContent = 'Fetch error: ' + String(err);
    }
  });

  // Signup creates a user account (direct) and stores JWT on success
  const signupBtn = document.getElementById('signupBtn');
  const signupOut = document.getElementById('signupOutput');
  signupBtn.addEventListener('click', async () => {
    const name = document.getElementById('signupName').value.trim();
    const email = document.getElementById('signupEmail').value.trim();
    const password = document.getElementById('signupPassword').value;
    const grade = document.getElementById('signupGrade').value;

    signupOut.textContent = 'Creating account...';
    try {
      const res = await apiFetch('/api/auth/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, password, grade })
      });
      let data;
      try { data = await res.json(); } catch(e) { data = null }
      if (!res.ok) {
        signupOut.textContent = `Signup failed (${res.status}): ${data ? JSON.stringify(data) : 'no details'}`;
        return;
      }
      // On success: store JWT and update UI
      if (data && data.token) {
        localStorage.setItem('jwt', data.token);
        document.getElementById('authOutput').textContent = `Logged in as ${data.user ? data.user.email : email}`;
      }
      signupOut.textContent = `Account created and logged in as ${email}`;
    } catch (err) {
      const msg = String(err) === 'TypeError: Failed to fetch'
        ? 'Network error: cannot reach backend. Is the backend running? Try `cd backend && python app.py` and serve frontend via `cd frontend && python -m http.server 8000`.'
        : 'Error: ' + String(err);
      signupOut.textContent = msg;
    }
  });

  // Admin: list, approve, reject teacher requests
  const refreshRequestsBtn = document.getElementById('refreshRequests');
  const clearRequestsBtn = document.getElementById('clearRequests');
  const requestsList = document.getElementById('requestsList');

  function renderRequests(items){
    if (!items || items.length === 0){
      requestsList.innerHTML = '<div>No pending requests</div>';
      return;
    }
    const html = items.map(r => `
      <div class="request-item card" data-id="${r.id}" style="display:flex;justify-content:space-between;align-items:center;padding:8px;margin-bottom:8px">
        <div>
          <strong>${r.name}</strong> <span style="color:#6b7280">${r.email}</span>
          <div style="font-size:12px;color:#6b7280">Grade: ${getGradeDisplayName(r.grade)} · ${new Date(r.created_at).toLocaleString()}</div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="approveBtn">Approve</button>
          <button class="rejectBtn secondary">Reject</button>
        </div>
      </div>
    `).join('');
    requestsList.innerHTML = html;

    // Attach handlers
    document.querySelectorAll('.approveBtn').forEach(btn => btn.addEventListener('click', async (e) => {
      const id = e.target.closest('.request-item').dataset.id;
      const token = getAdminToken();
      try {
        const res = await apiFetch(`/api/admin/teacher-requests/${id}/approve`, { method: 'POST', headers: { 'X-Admin-Token': token } });
        if (!res.ok) {
          const t = await res.text().catch(()=>null);
          alert(`Failed to approve: ${res.status} ${t || ''}`);
          return;
        }
        alert('Approved');
        refreshRequestsBtn.click();
      } catch (err) { alert('Error: '+String(err)); }
    }));

    document.querySelectorAll('.rejectBtn').forEach(btn => btn.addEventListener('click', async (e) => {
      const id = e.target.closest('.request-item').dataset.id;
      const token = getAdminToken();
      try {
        const res = await apiFetch(`/api/admin/teacher-requests/${id}/reject`, { method: 'POST', headers: { 'X-Admin-Token': token } });
        if (!res.ok) {
          const t = await res.text().catch(()=>null);
          alert(`Failed to reject: ${res.status} ${t || ''}`);
          return;
        }
        alert('Rejected');
        refreshRequestsBtn.click();
      } catch (err) { alert('Error: '+String(err)); }
    }));
  }

  // Pagination state (requests)
  let currentPage = 1;
  const perPage = 5;

  async function loadRequests(page = 1){
    const token = getAdminToken();
    requestsList.textContent = 'Loading...';
    try {
      const res = await apiFetch(`/api/admin/teacher-requests?page=${page}&per_page=${perPage}`, { headers: { 'X-Admin-Token': token } });
      if (!res.ok) {
        const t = await res.text().catch(()=>null);
        requestsList.textContent = `Failed to load (${res.status}): ${t || ''}`;
        return;
      }
      const data = await res.json();
      renderRequests(data.items || []);
      document.getElementById('requestsPage').textContent = `Page: ${data.page} / ${Math.ceil((data.total||0)/data.per_page) || 1}`;
      currentPage = data.page || 1;
    } catch (err) {
      requestsList.textContent = 'Error: ' + String(err);
    }
  }

  refreshRequestsBtn.addEventListener('click', async () => { await loadRequests(1); });

  document.getElementById('prevPage').addEventListener('click', async () => { if (currentPage > 1) await loadRequests(currentPage-1); });
  document.getElementById('nextPage').addEventListener('click', async () => { await loadRequests(currentPage+1); });

  clearRequestsBtn.addEventListener('click', () => { requestsList.innerHTML = 'No requests loaded'; });

  // --- Test results UI ---
  const refreshResultsBtn = document.getElementById('refreshResults');
  const clearResultsBtn = document.getElementById('clearResults');
  const resultsList = document.getElementById('resultsList');
  const resultsPageLabel = document.getElementById('resultsPage');
  let resultsPage = 1;
  const resultsPerPage = 8;

  function renderResults(items){
    if (!items || items.length === 0){
      resultsList.innerHTML = '<div>No results found</div>';
      return;
    }
    const rows = items.map(r => `
      <div class="result-row card" data-user-id="${r.user?.id || ''}" style="display:flex;justify-content:space-between;align-items:center;padding:8px;margin-bottom:8px">
        <div>
          <strong>${r.user?.name || 'Unknown'}</strong> <span style="color:#6b7280">${r.user?.email || ''}</span>
          <div style="font-size:12px;color:#6b7280">${r.subject} · ${r.total_questions} q · ${r.score_pct}%${r.avg_time_per_question ? ` · avg ${r.avg_time_per_question}s` : ''} · ${new Date(r.started_at).toLocaleString()}</div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="detailsBtn">History</button>
        </div>
      </div>
    `).join('');
    resultsList.innerHTML = rows;

    document.querySelectorAll('.detailsBtn').forEach(btn => btn.addEventListener('click', async (e) => {
      const userId = e.target.closest('.result-row').dataset.userId;
      await showUserHistory(userId);
    }));
  }

  async function loadResults(page = 1){
    const token = getAdminToken();
    const subject = document.getElementById('filterSubject').value.trim();
    const email = document.getElementById('filterEmail').value.trim();
    const start = document.getElementById('filterStart').value;
    const end = document.getElementById('filterEnd').value;
    resultsList.textContent = 'Loading...';
    try {
      const res = await apiFetch(`/api/admin/test-results?page=${page}&per_page=${resultsPerPage}${subject?`&subject=${encodeURIComponent(subject)}`:''}${email?`&email=${encodeURIComponent(email)}`:''}${start?`&start=${encodeURIComponent(start)}`:''}${end?`&end=${encodeURIComponent(end)}`:''}`, { headers: { 'X-Admin-Token': token } });
      if (!res.ok){
        const t = await res.text().catch(()=>null);
        resultsList.textContent = `Failed to load (${res.status}): ${t || ''}`;
        return;
      }
      const data = await res.json();
      renderResults(data.items || []);
      resultsPageLabel.textContent = `Page: ${data.page} / ${Math.ceil((data.total||0)/data.per_page) || 1}`;
      resultsPage = data.page || 1;
    } catch (err){
      resultsList.textContent = 'Error: ' + String(err);
    }
  }

  refreshResultsBtn.addEventListener('click', async () => { await loadResults(1); });

  const exportBtn = document.getElementById('exportCsv');
  if (exportBtn) {
    exportBtn.title = 'Includes column avg_time_per_question (seconds)';
    exportBtn.addEventListener('click', async () => {
      const token = getAdminToken();
      const subject = document.getElementById('filterSubject').value.trim();
      const email = document.getElementById('filterEmail').value.trim();
      const start = document.getElementById('filterStart').value;
      const end = document.getElementById('filterEnd').value;
      try {
        const url = `/api/admin/test-results?format=csv${subject?`&subject=${encodeURIComponent(subject)}`:''}${email?`&email=${encodeURIComponent(email)}`:''}${start?`&start=${encodeURIComponent(start)}`:''}${end?`&end=${encodeURIComponent(end)}`:''}`;
        const res = await apiFetch(url, { headers: { 'X-Admin-Token': token } });
        if (!res.ok) {
          const t = await res.text().catch(()=>null);
          alert(`Export failed: ${res.status} ${t || ''}`);
          return;
        }
        const csv = await res.text();
        const blob = new Blob([csv], { type: 'text/csv' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `test_results_${new Date().toISOString().slice(0,10)}.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
      } catch (err) {
        alert('Export error: '+String(err));
      }
    });
  }
  document.getElementById('resultsPrev').addEventListener('click', async () => { if (resultsPage > 1) await loadResults(resultsPage-1); });
  document.getElementById('resultsNext').addEventListener('click', async () => { await loadResults(resultsPage+1); });
  clearResultsBtn.addEventListener('click', () => { resultsList.innerHTML = 'No results loaded'; });

  // Modal helpers
  const resultsModal = document.getElementById('resultsModal');
  const resultsModalTitle = document.getElementById('resultsModalTitle');
  const resultsModalBody = document.getElementById('resultsModalBody');
  document.getElementById('resultsClose').addEventListener('click', () => { resultsModal.classList.add('hidden'); resultsModal.setAttribute('aria-hidden','true'); });

  function drawSparkline(scores){
    if (!scores || scores.length === 0) return '<div>No history</div>';
    const w = 300, h = 80, padding = 6;
    const max = Math.max(...scores), min = Math.min(...scores);
    const pts = scores.map((s,i)=>{
      const x = padding + (i/(scores.length-1 || 1))*(w-2*padding);
      const y = h - padding - ((s - min) / ((max-min)||1))*(h-2*padding);
      return `${x},${y}`;
    }).join(' ');
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg">
      <polyline points="${pts}" fill="none" stroke="#2563eb" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
      ${scores.map((s,i)=>{const x=padding+(i/(scores.length-1||1))*(w-2*padding);const y=h-padding-((s-min)/((max-min)||1))*(h-2*padding);return `<circle cx="${x}" cy="${y}" r="3" fill="#fff" stroke="#2563eb" />`;}).join('')}
    </svg>`;
  }

  // Chart.js instances
  let scoresChart = null;
  let perTestChart = null;

  async function showUserHistory(userId){
    resultsModal.classList.remove('hidden');
    resultsModal.setAttribute('aria-hidden','false');
    resultsModalBody.textContent = 'Loading...';
    try{
      const token = getAdminToken();
      const res = await apiFetch(`/api/admin/test-results/${userId}/history`, { headers: { 'X-Admin-Token': token } });
      if(!res.ok){
        const t = await res.text().catch(()=>null);
        resultsModalBody.textContent = `Failed to load (${res.status}): ${t || ''}`;
        return;
      }
      const data = await res.json();
      const items = data.items || [];

      // Scores over time (reverse to chronological)
      const chronological = items.slice().reverse();
      const labels = chronological.map(i => i.started_at ? new Date(i.started_at).toLocaleDateString() : '');
      const scores = chronological.map(i => i.score_pct);

      resultsModalTitle.textContent = items[0] ? `${items[0].subject} history for ${items.length} tests` : 'Student history';

      // Render scores line chart
      const scoresCtx = document.getElementById('scoresChart').getContext('2d');
      if (scoresChart) scoresChart.destroy();
      scoresChart = new Chart(scoresCtx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'Score %',
            data: scores,
            borderColor: '#2563eb',
            backgroundColor: 'rgba(37,99,235,0.08)',
            fill: true,
            tension: 0.2,
            pointRadius: 4
          }]
        },
        options: {
          responsive: true,
          scales: { y: { suggestedMin: 0, suggestedMax: 100 } }
        }
      });

      // Render history list with a View button per test
      const listHtml = chronological.map(i=>`<div style="padding:6px 0;display:flex;justify-content:space-between;align-items:center">
          <div><strong>${i.subject}</strong> — ${i.score_pct}% (${i.correct_answers}/${i.total_questions})${i.avg_time_per_question ? ` · avg ${i.avg_time_per_question}s` : ''} · ${i.started_at ? new Date(i.started_at).toLocaleString() : ''}</div>
          <div><button class="viewTestBtn" data-test-id="${i.id}">View Test</button></div>
        </div>`).join('');
      document.getElementById('historyList').innerHTML = listHtml;

      // Attach handlers for viewing a specific test's answers
      document.querySelectorAll('.viewTestBtn').forEach(btn => btn.addEventListener('click', async (e) => {
        const testId = e.target.dataset.testId;
        await loadTestDetail(testId);
      }));

    }catch(err){
      resultsModalBody.textContent = 'Error: '+String(err);
    }
  }

  async function loadTestDetail(testId){
    const token = getAdminToken();
    const target = document.getElementById('perTestDetails');
    target.textContent = 'Loading test details...';
    try{
      const res = await apiFetch(`/api/admin/test-results/${testId}`, { headers: { 'X-Admin-Token': token } });
      if(!res.ok){
        const t = await res.text().catch(()=>null);
        target.textContent = `Failed to load (${res.status}): ${t || ''}`;
        return;
      }
      const data = await res.json();
      const answers = data.answers || [];
      if (answers.length === 0){
        target.textContent = 'No answers recorded for this test.';
        return;
      }

      // Build per-question chart data (0 or 100 for wrong/correct)
      const labels = answers.map((a, idx) => `Q${idx+1}`);
      const values = answers.map(a => a.is_correct ? 100 : 0);

      // Destroy previous chart
      const perCtx = document.getElementById('perTestChart').getContext('2d');
      if (perTestChart) perTestChart.destroy();
      perTestChart = new Chart(perCtx, {
        type: 'bar',
        data: {
          labels: labels,
          datasets: [{
            label: 'Correct (100 = correct, 0 = wrong)',
            data: values,
            backgroundColor: values.map(v => v===100 ? 'rgba(16,185,129,0.8)' : 'rgba(239,68,68,0.8)')
          }]
        },
        options: {
          responsive: true,
          scales: { y: { suggestedMin: 0, suggestedMax: 100 } }
        }
      });

      // Show detailed list
      const detailsHtml = answers.map((a, i) => `
        <div style="padding:6px 0">
          <strong>Q${i+1}:</strong> ${a.question_text || '—'} — <em>${a.is_correct ? 'Correct' : 'Wrong'}</em> · ${a.time_spent}s · ${a.emotion_at_time || '—'}
        </div>
      `).join('');
      document.getElementById('perTestDetails').innerHTML = `<div style="margin-bottom:8px"><strong>Test ${data.test.id}</strong> — ${data.test.score_pct}%${data.test.avg_time_per_question ? ` · avg ${data.test.avg_time_per_question}s` : ''}</div>${detailsHtml}`;

    }catch(err){
      target.textContent = 'Error: '+String(err);
    }
  }

  // Login
  const loginBtn = document.getElementById('loginBtn');
  const loginOut = document.getElementById('loginOutput');
  loginBtn.addEventListener('click', async () => {
    const email = document.getElementById('loginEmail').value.trim();
    const password = document.getElementById('loginPassword').value;
    loginOut.textContent = 'Logging in...';
    try {
      const res = await apiFetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
      });
      let data;
      try { data = await res.json(); } catch(e){ data = null }
      if (!res.ok) {
        loginOut.textContent = `Login failed (${res.status}): ${data ? JSON.stringify(data) : 'no details'}`;
        return;
      }
      if (data && data.token) localStorage.setItem('jwt', data.token);
      loginOut.textContent = `Logged in: ${data ? JSON.stringify(data.user) : 'ok'}`;
      document.getElementById('authOutput').textContent = data && data.user ? `Logged in as ${data.user.email}` : 'Logged in';
    } catch (err) {
      const msg = String(err) === 'TypeError: Failed to fetch'
        ? 'Network error: cannot reach backend. Is the backend running? Try `cd backend && python app.py` and serve frontend via `cd frontend && python -m http.server 8000`.'
        : 'Error: ' + String(err);
      loginOut.textContent = msg;
    }
  });

  // Authenticated actions
  const whoamiBtn = document.getElementById('whoami');
  const getSummaryBtn = document.getElementById('getSummary');
  const logoutBtn = document.getElementById('logoutBtn');
  const authOut = document.getElementById('authOutput');

  function getAuthHeader() {
    const token = localStorage.getItem('jwt');
    return token ? { 'Authorization': 'Bearer ' + token } : {};
  }

  // Quick server health check to aid debugging
  const apiUrlEl = document.getElementById('apiUrl');
  async function checkServer(){
    const statusEl = document.getElementById('serverStatus');
    apiUrlEl.textContent = `API: ${API_BASE || location.origin}`;
    try {
      const res = await apiFetch('/health');
      const text = await res.text().catch(()=>null);
      if (!res.ok) {
        statusEl.textContent = `Server: Unhealthy (${res.status})`;
        statusEl.style.color='orange';
        return text ? `Details: ${text}` : undefined;
      }
      // Try parse JSON, fallback to raw text
      let data;
      try { data = JSON.parse(text); } catch(e){ data = null }
      statusEl.textContent = `Server: ${data?.status || 'ok'}`;
      statusEl.style.color = 'green';
      return data || text;
    } catch (err) {
      statusEl.textContent = 'Server: unreachable (start backend)';
      statusEl.style.color = 'red';
      return String(err);
    }
  }
  // Run initial check
  (async()=>{ await checkServer(); })();

  // Retry button
  document.getElementById('healthRetry').addEventListener('click', async () => {
    const r = await checkServer();
    if (r) console.info('Health check result:', r);
  });

  whoamiBtn.addEventListener('click', async () => {
    authOut.textContent = 'Calling /api/auth/me...';
    try {
      const res = await apiFetch('/api/auth/me', { headers: getAuthHeader() });
      const data = await res.json();
      authOut.textContent = res.ok ? JSON.stringify(data) : `Error ${res.status}: ${JSON.stringify(data)}`;
    } catch (err) {
      authOut.textContent = 'Error: ' + String(err);
    }
  });

  getSummaryBtn.addEventListener('click', async () => {
    authOut.textContent = 'Fetching my summary...';
    try {
      const res = await apiFetch('/api/reports/summary', { headers: getAuthHeader() });
      const data = await res.json();
      authOut.textContent = res.ok ? JSON.stringify(data, null, 2) : `Error ${res.status}: ${JSON.stringify(data)}`;
    } catch (err) {
      authOut.textContent = 'Error: ' + String(err);
    }
  });

  logoutBtn.addEventListener('click', () => {
    localStorage.removeItem('jwt');
    authOut.textContent = 'Logged out';
    loginOut.textContent = 'Not logged in';
    signupOut.textContent = 'Not logged in';
  });
});
