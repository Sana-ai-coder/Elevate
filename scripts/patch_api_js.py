"""Patch the admin section of frontend/js/api.js with extended methods."""
import os

api_path = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'js', 'api.js')

with open(api_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find boundaries of the admin section
start_marker = '  // Admin endpoints'
# The admin section ends with '},\r\n\r\n  student:' (or '},\n\n  student:')
end_candidates = ['  },\r\n\r\n  student:', '  },\n\n  student:']

start_idx = content.find(start_marker)
end_idx = -1
end_len = 0
for ec in end_candidates:
    idx = content.find(ec, start_idx)
    if idx != -1:
        end_idx = idx + len('  },')  # cut off after the closing brace
        end_len = len(ec) - len('  },')
        break

if start_idx == -1 or end_idx == -1:
    print('ERROR: Could not find admin section boundaries')
    print('start_idx:', start_idx, 'end_idx:', end_idx)
    exit(1)

print(f'Found admin section: chars {start_idx} to {end_idx}')

NEW_ADMIN = '''  // Admin endpoints (Phases 1-8 extended)
  admin: {
    async getStats() {
      return await api.request('/admin/stats', { method: 'GET' });
    },
    async listTeacherRequests(params = {}) {
      const q = new URLSearchParams();
      if (params.page) q.append('page', String(params.page));
      if (params.per_page) q.append('per_page', String(params.per_page));
      if (params.status) q.append('status', params.status);
      return await api.request('/admin/teacher-requests' + (q.toString() ? '?' + q.toString() : ''), { method: 'GET' });
    },
    async approveTeacherRequest(id) {
      return await api.request('/admin/teacher-requests/' + id + '/approve', { method: 'POST' });
    },
    async rejectTeacherRequest(id) {
      return await api.request('/admin/teacher-requests/' + id + '/reject', { method: 'POST' });
    },
    async listTestResults(params = {}) {
      const q = new URLSearchParams();
      if (params.page) q.append('page', String(params.page));
      if (params.per_page) q.append('per_page', String(params.per_page));
      if (params.subject) q.append('subject', params.subject);
      if (params.email) q.append('email', params.email);
      if (params.start) q.append('start', params.start);
      if (params.end) q.append('end', params.end);
      if (params.school_id) q.append('school_id', String(params.school_id));
      if (params.status) q.append('status', params.status);
      if (params.min_score !== undefined) q.append('min_score', String(params.min_score));
      if (params.max_score !== undefined) q.append('max_score', String(params.max_score));
      return await api.request('/admin/test-results' + (q.toString() ? '?' + q.toString() : ''), { method: 'GET' });
    },
    async getTestResultHistory(userId) {
      return await api.request('/admin/test-results/' + userId + '/history', { method: 'GET' });
    },
    async getTestResultDetail(testResultId) {
      return await api.request('/admin/test-results/' + testResultId, { method: 'GET' });
    },
    async exportTestResultsCsv(params = {}) {
      const q = new URLSearchParams();
      q.append('format', 'csv');
      if (params.subject) q.append('subject', params.subject);
      if (params.email) q.append('email', params.email);
      if (params.start) q.append('start', params.start);
      if (params.end) q.append('end', params.end);
      const token = api.getToken();
      const ep = '/admin/test-results?' + q.toString();
      const url = config.API_BASE_URL + ep + '&_ts=' + Date.now();
      const res = await fetch(url, { method: 'GET', headers: token ? { Authorization: 'Bearer ' + token } : {}, cache: 'no-store' });
      if (!res.ok) { const t = await res.text().catch(() => ''); throw new Error(api.buildFriendlyApiError(ep, 'GET', res.status, t)); }
      return await res.text();
    },
    // Phase 2: User management
    async listUsers(params = {}) {
      const q = new URLSearchParams();
      if (params.page) q.append('page', String(params.page));
      if (params.per_page) q.append('per_page', String(params.per_page));
      if (params.role) q.append('role', params.role);
      if (params.school_id) q.append('school_id', String(params.school_id));
      if (params.status) q.append('status', params.status);
      if (params.search) q.append('search', params.search);
      return await api.request('/admin/users' + (q.toString() ? '?' + q.toString() : ''), { method: 'GET' });
    },
    async updateUser(userId, payload) {
      return await api.request('/admin/users/' + userId, { method: 'PATCH', body: JSON.stringify(payload || {}) });
    },
    async disableUser(userId, reason) {
      return await api.request('/admin/users/' + userId + '/disable', { method: 'POST', body: JSON.stringify({ reason: reason || '' }) });
    },
    async enableUser(userId) {
      return await api.request('/admin/users/' + userId + '/enable', { method: 'POST' });
    },
    // Phase 3: School hierarchy
    async getSchoolsHierarchy() { return await api.request('/admin/schools/hierarchy', { method: 'GET' }); },
    async getSchoolHierarchyDetail(schoolId) { return await api.request('/admin/schools/' + schoolId + '/hierarchy', { method: 'GET' }); },
    async listSchools() { return await api.request('/admin/schools', { method: 'GET' }); },
    async createSchool(payload) { return await api.request('/admin/schools', { method: 'POST', body: JSON.stringify(payload || {}) }); },
    async deleteSchool(schoolId) { return await api.request('/admin/schools/' + schoolId, { method: 'DELETE' }); },
    // Phase 4: Training monitor
    async triggerTraining() { return await api.request('/admin/ml/train-strict', { method: 'POST', timeoutMs: 60000 }); },
    async getTrainingStatus(jobId) { return await api.request('/admin/ml/train-strict/' + jobId, { method: 'GET' }); },
    async listTrainingJobs(params = {}) {
      const q = new URLSearchParams();
      if (params.page) q.append('page', String(params.page));
      if (params.per_page) q.append('per_page', String(params.per_page));
      if (params.status) q.append('status', params.status);
      return await api.request('/admin/ml/jobs' + (q.toString() ? '?' + q.toString() : ''), { method: 'GET' });
    },
    async getTrainingJob(id) { return await api.request('/admin/ml/jobs/' + id, { method: 'GET' }); },
    // Phase 5: Model versions
    async listModelVersions(params = {}) {
      const q = new URLSearchParams();
      if (params.model_name) q.append('model_name', params.model_name);
      if (params.page) q.append('page', String(params.page));
      if (params.per_page) q.append('per_page', String(params.per_page));
      return await api.request('/admin/ml/versions' + (q.toString() ? '?' + q.toString() : ''), { method: 'GET' });
    },
    async createModelVersion(payload) { return await api.request('/admin/ml/versions', { method: 'POST', body: JSON.stringify(payload || {}) }); },
    async promoteModelVersion(vid) { return await api.request('/admin/ml/versions/' + vid + '/promote', { method: 'POST' }); },
    async setRollbackTarget(vid) { return await api.request('/admin/ml/versions/' + vid + '/set-rollback-target', { method: 'POST' }); },
    async compareModelVersions(a, b) { return await api.request('/admin/ml/versions/compare?a=' + a + '&b=' + b, { method: 'GET' }); },
    // Phase 6: MCQ observability
    async getMcqObservability(params = {}) {
      const q = new URLSearchParams();
      if (params.days) q.append('days', String(params.days));
      if (params.subject) q.append('subject', params.subject);
      if (params.school_id) q.append('school_id', String(params.school_id));
      return await api.request('/admin/mcq/observability' + (q.toString() ? '?' + q.toString() : ''), { method: 'GET' });
    },
    // Phase 7: Audit logs
    async listAuditLogs(params = {}) {
      const q = new URLSearchParams();
      if (params.page) q.append('page', String(params.page));
      if (params.per_page) q.append('per_page', String(params.per_page));
      if (params.action) q.append('action', params.action);
      if (params.target_type) q.append('target_type', params.target_type);
      if (params.actor_id) q.append('actor_id', String(params.actor_id));
      if (params.date_from) q.append('date_from', params.date_from);
      if (params.date_to) q.append('date_to', params.date_to);
      return await api.request('/admin/audit-logs' + (q.toString() ? '?' + q.toString() : ''), { method: 'GET' });
    },
    async exportAuditLogs() {
      const token = api.getToken();
      const url = config.API_BASE_URL + '/admin/audit-logs/export?_ts=' + Date.now();
      const res = await fetch(url, { method: 'GET', headers: token ? { Authorization: 'Bearer ' + token } : {}, cache: 'no-store' });
      if (!res.ok) throw new Error('Export failed: ' + res.status);
      return await res.text();
    },
  }'''

new_content = content[:start_idx] + NEW_ADMIN + content[end_idx:]

with open(api_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print(f'Done! Wrote {len(new_content)} chars to {api_path}')
