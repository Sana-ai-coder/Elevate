function adminHeader() {
  const token = document.getElementById('adminToken') ? document.getElementById('adminToken').value : '';
  return token ? { 'X-Admin-Token': token, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
}

async function loadSchools() {
  const res = await fetch('/api/admin/schools', { headers: adminHeader() });
  if (!res.ok) {
    document.getElementById('schoolsList').textContent = 'Failed to load schools (check admin token)';
    return [];
  }
  const data = await res.json();
  const container = document.getElementById('schoolsList');
  container.innerHTML = '';
  data.items.forEach(s => {
    const div = document.createElement('div');
    div.style.display = 'flex';
    div.style.justifyContent = 'space-between';
    div.style.padding = '6px 0';
    div.innerHTML = `<div><strong>${s.name}</strong> <small style="color:#666">(id:${s.id} slug:${s.slug||''})</small></div><div><button data-id="${s.id}" class="delSchool">Delete</button></div>`;
    container.appendChild(div);
  });
  Array.from(document.getElementsByClassName('delSchool')).forEach(btn => btn.addEventListener('click', async (e) => {
    const id = e.target.getAttribute('data-id');
    if (!confirm('Delete school?')) return;
    const r = await fetch('/api/admin/schools/' + id, { method: 'DELETE', headers: adminHeader() });
    if (r.ok) { loadSchools(); } else { alert('Failed to delete'); }
  }));
  return data.items;
}

async function createSchool() {
  const name = document.getElementById('schoolName').value;
  const slug = document.getElementById('schoolSlug').value;
  const res = await fetch('/api/admin/schools', { method: 'POST', headers: adminHeader(), body: JSON.stringify({name, slug}) });
  if (!res.ok) {
    document.getElementById('schoolMsg').textContent = 'Error creating school';
    return;
  }
  document.getElementById('schoolMsg').textContent = 'Created';
  document.getElementById('schoolName').value = '';
  document.getElementById('schoolSlug').value = '';
  await loadSchools();
}

async function loadUsers() {
  const id = document.getElementById('filterSchoolId').value;
  const url = '/api/admin/users' + (id ? '?school_id=' + encodeURIComponent(id) : '');
  const res = await fetch(url, { headers: adminHeader() });
  if (!res.ok) { document.getElementById('usersList').textContent = 'Failed to load users'; return []; }
  const data = await res.json();
  const c = document.getElementById('usersList');
  c.innerHTML = '';
  data.items.forEach(u => {
    const d = document.createElement('div');
    d.style.padding = '6px 0';
    d.innerHTML = `<div><strong>${u.name}</strong> (${u.email}) — role: <select data-id="${u.id}" class="roleSel"><option value="student">student</option><option value="teacher">teacher</option><option value="admin">admin</option></select> school: <input data-id="${u.id}" class="schoolInput" value="${u.school_id||''}" style="width:60px"/> <button data-id="${u.id}" class="saveUser">Save</button> <button data-id="${u.id}" class="delUser">Delete</button></div>`;
    c.appendChild(d);
  });
  Array.from(document.getElementsByClassName('saveUser')).forEach(btn => btn.addEventListener('click', async (e) => {
    const id = e.target.getAttribute('data-id');
    const role = document.querySelector(`.roleSel[data-id='${id}']`).value;
    const school_id = document.querySelector(`.schoolInput[data-id='${id}']`).value || null;
    const r = await fetch('/api/admin/users/' + id, { method: 'PUT', headers: adminHeader(), body: JSON.stringify({role, school_id}) });
    if (r.ok) loadUsers(); else alert('Failed to save');
  }));
  Array.from(document.getElementsByClassName('delUser')).forEach(btn => btn.addEventListener('click', async (e) => {
    const id = e.target.getAttribute('data-id');
    if (!confirm('Delete user?')) return;
    const r = await fetch('/api/admin/users/' + id, { method: 'DELETE', headers: adminHeader() });
    if (r.ok) loadUsers(); else alert('Failed to delete');
  }));
}

// Wire UI
window.addEventListener('load', () => {
  document.getElementById('createSchool').addEventListener('click', createSchool);
  document.getElementById('refreshUsers').addEventListener('click', loadUsers);
  loadSchools();
});
