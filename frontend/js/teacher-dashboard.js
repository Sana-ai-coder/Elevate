import { auth } from './auth.js';
import { api } from './api.js';
import { utils } from './utils.js';

const charts = {};
const teacherCache = {
  tests: [],
  students: [],
  classrooms: [],
  assignments: [],
  reportRows: [],
};

function getKnownStudents() {
  const byId = new Map();

  const addStudent = (student) => {
    if (!student || !student.id) return;
    byId.set(Number(student.id), {
      id: Number(student.id),
      name: student.name || `Student #${student.id}`,
      email: student.email || '',
      grade: student.grade || '',
      attempts: Number(student.attempts || 0),
      avg_score: Number(student.avg_score || 0),
    });
  };

  (teacherCache.students || []).forEach(addStudent);
  (teacherCache.classrooms || []).forEach(classroom => {
    (classroom.students || []).forEach(addStudent);
  });
  (teacherCache.assignments || []).forEach(assignment => {
    if (assignment && assignment.student) {
      addStudent(assignment.student);
    }
  });

  return Array.from(byId.values()).sort((a, b) => String(a.name).localeCompare(String(b.name)));
}

const STEM_SUBJECT_TOPICS = {
  science: ['physics', 'chemistry', 'biology', 'earth science', 'environment'],
  technology: ['computer science', 'programming', 'data', 'networks', 'ai fundamentals'],
  engineering: ['mechanics', 'circuits', 'design process', 'materials', 'robotics'],
  mathematics: ['algebra', 'arithmetic', 'geometry', 'trigonometry', 'calculus'],
};

function getSession() {
  return auth.loadSession();
}

function ensureTeacherSession() {
  const session = getSession();
  if (!session || !session.user || !session.token) {
    window.location.replace('index.html');
    return null;
  }

  const role = session.user.role;
  if (role !== 'teacher' && role !== 'admin') {
    window.location.replace('dashboard.html');
    return null;
  }

  return session;
}

function setBusy(button, busyText) {
  if (!button) return;
  if (!button.dataset.defaultText) {
    button.dataset.defaultText = button.innerHTML;
  }
  button.disabled = true;
  button.innerHTML = `<i class="fas fa-spinner fa-spin me-1"></i>${busyText}`;
}

function clearBusy(button) {
  if (!button) return;
  button.disabled = false;
  button.innerHTML = button.dataset.defaultText || button.innerHTML;
}

function formatDate(value) {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '-';
  return d.toLocaleString();
}

function formatGradeDisplay(value, fallback = '-') {
  if (!value) return fallback;
  return utils.getGradeDisplayName(String(value)) || fallback;
}

function formatPercent(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function formatGenerationDiagnostics(generationStatus) {
  const status = generationStatus && typeof generationStatus === 'object' ? generationStatus : {};
  const serviceStatus = status.service_status_code ?? '-';
  const latencyValue = Number(status.service_latency_ms);
  const latencyLabel = Number.isFinite(latencyValue) && latencyValue >= 0 ? `${latencyValue}ms` : 'n/a';
  const serviceGenerated = Number(status.service_generated_count || 0);
  const localFallback = Number(status.local_fallback_count || 0);

  const parts = [
    `HTTP ${serviceStatus}`,
    `Latency ${latencyLabel}`,
    `Service ${serviceGenerated}`,
  ];
  if (localFallback > 0) parts.push(`Fallback ${localFallback}`);
  if (status.llm_only === true) parts.push('LLM only');

  return {
    summary: parts.join(' | '),
    endpoint: status.service_endpoint ? String(status.service_endpoint) : '',
    error: status.service_error ? String(status.service_error) : '',
  };
}

function toTitleCase(value) {
  return String(value || '-')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, char => char.toUpperCase());
}

function toDateTimeLocalValue(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';

  const pad = n => String(n).padStart(2, '0');
  const yyyy = d.getFullYear();
  const mm = pad(d.getMonth() + 1);
  const dd = pad(d.getDate());
  const hh = pad(d.getHours());
  const mi = pad(d.getMinutes());
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}`;
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function collectEditableQuestionsFromPanel(panel) {
  if (!panel) return [];
  const rows = Array.from(panel.querySelectorAll('.test-question-editor-row'));
  return rows.map((row, index) => {
    const id = Number(row.dataset.questionId || 0);
    const text = row.querySelector('.q-text')?.value?.trim() || '';
    const order = Number(row.querySelector('.q-order')?.value || (index + 1));
    const points = Number(row.querySelector('.q-points')?.value || 1);

    const options = [0, 1, 2, 3].map(i => row.querySelector(`.q-opt-${i}`)?.value?.trim() || '');
    const correctIndex = Number(row.querySelector('.q-correct')?.value || 0);

    return {
      id,
      text,
      order,
      points,
      options,
      correct_index: correctIndex,
    };
  });
}

function renumberQuestionCards(panel) {
  const cards = Array.from(panel.querySelectorAll('.test-question-editor-row'));
  cards.forEach((card, index) => {
    const order = index + 1;
    const serialEl = card.querySelector('.question-serial-number');
    const orderInput = card.querySelector('.q-order');
    if (serialEl) serialEl.textContent = String(order);
    if (orderInput) orderInput.value = String(order);
  });
}

function enableQuestionCardDragAndDrop(panel) {
  if (!panel) return;
  const list = panel.querySelector('.test-question-editor-list');
  if (!list) return;

  let draggedCard = null;
  const cards = Array.from(list.querySelectorAll('.test-question-editor-row'));

  cards.forEach(card => {
    card.setAttribute('draggable', 'true');

    card.addEventListener('dragstart', evt => {
      draggedCard = card;
      card.classList.add('is-dragging');
      if (evt.dataTransfer) {
        evt.dataTransfer.effectAllowed = 'move';
        evt.dataTransfer.setData('text/plain', card.dataset.questionId || '');
      }
    });

    card.addEventListener('dragend', () => {
      card.classList.remove('is-dragging');
      draggedCard = null;
      list.querySelectorAll('.drop-target-before, .drop-target-after').forEach(el => {
        el.classList.remove('drop-target-before', 'drop-target-after');
      });
      renumberQuestionCards(panel);
    });

    card.addEventListener('dragover', evt => {
      evt.preventDefault();
      if (!draggedCard || draggedCard === card) return;
      const rect = card.getBoundingClientRect();
      const insertAfter = evt.clientY > rect.top + rect.height / 2;
      card.classList.toggle('drop-target-before', !insertAfter);
      card.classList.toggle('drop-target-after', insertAfter);

      if (insertAfter) {
        list.insertBefore(draggedCard, card.nextSibling);
      } else {
        list.insertBefore(draggedCard, card);
      }
    });

    card.addEventListener('dragleave', () => {
      card.classList.remove('drop-target-before', 'drop-target-after');
    });
  });

  renumberQuestionCards(panel);
}

function activateSection(targetId) {
  const nav = document.getElementById('teacherNav');
  if (!nav) return;
  const contentArea = document.getElementById('teacherContentArea');

  document.querySelectorAll('.teacher-section').forEach(section => {
    section.classList.remove('active');
  });

  const section = document.getElementById(targetId);
  if (section) {
    section.classList.add('active');
  }

  nav.querySelectorAll('.nav-link').forEach(item => {
    const isActive = item.dataset.target === targetId;
    item.classList.toggle('active', isActive);
    item.setAttribute('aria-current', isActive ? 'page' : 'false');
  });

  if (contentArea) {
    contentArea.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

async function refreshSectionData(targetId) {
  try {
    if (targetId === 'overviewSection') {
      await loadOverview();
      return;
    }
    if (targetId === 'testsSection') {
      await loadTests();
      syncAssignmentFormOptions();
      return;
    }
    if (targetId === 'studentsSection') {
      await loadStudents();
      renderClassroomsTable();
      syncAssignmentFormOptions();
      return;
    }
    if (targetId === 'classroomsSection') {
      await Promise.all([loadClassrooms(), loadStudents()]);
      syncAssignmentFormOptions();
      return;
    }
    if (targetId === 'assignmentsSection') {
      await Promise.all([loadAssignments(), loadTests(), loadClassrooms(), loadStudents()]);
      syncAssignmentFormOptions();
      return;
    }
    if (targetId === 'reportsSection') {
      await loadReports();
      return;
    }
    if (targetId === 'analyticsSection') {
      await loadAnalytics();
    }
  } catch (error) {
    console.error(error);
    utils.showNotification(error.message || 'Failed to refresh section data.', 'error');
  }
}

function bindSidebarNav() {
  const nav = document.getElementById('teacherNav');
  if (!nav) return;

  nav.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', evt => {
      evt.preventDefault();
      const targetId = link.dataset.target;
      activateSection(targetId);
      window.location.hash = targetId;
      refreshSectionData(targetId).catch(() => {});
    });
  });

  const initialHash = (window.location.hash || '').replace('#', '');
  if (initialHash && document.getElementById(initialHash)) {
    activateSection(initialHash);
  } else {
    activateSection('overviewSection');
  }
}

function setSelectOptions(selectEl, options, includeAny = false, anyLabel = 'All Subjects') {
  if (!selectEl) return;
  const normalized = Array.from(new Set((options || []).map(v => String(v || '').trim()).filter(Boolean)));
  const rows = [];
  if (includeAny) {
    rows.push('<option value="">' + anyLabel + '</option>');
  }
  normalized.forEach(value => {
    rows.push(`<option value="${escapeHtml(value)}">${escapeHtml(value.charAt(0).toUpperCase() + value.slice(1))}</option>`);
  });
  selectEl.innerHTML = rows.join('');
}

function syncReportSubjectOptions() {
  const reportSubject = document.getElementById('reportSubject');
  if (!reportSubject) return;

  const selected = reportSubject.value;
  const fromTests = (teacherCache.tests || []).map(t => String(t.subject || '').trim()).filter(Boolean);
  const fromReports = (teacherCache.reportRows || []).map(r => String(r.subject || '').trim()).filter(Boolean);
  const source = [...fromTests, ...fromReports];

  if (source.length === 0) {
    reportSubject.innerHTML = '<option value="">All Subjects</option>';
    return;
  }

  const uniq = Array.from(new Set(source));
  reportSubject.innerHTML = '<option value="">All Subjects</option>' + uniq.map(subj => {
    return `<option value="${escapeHtml(subj)}">${escapeHtml(subj)}</option>`;
  }).join('');

  if (selected && uniq.includes(selected)) {
    reportSubject.value = selected;
  }
}

async function refreshTopicDropdown() {
  const subject = document.getElementById('testSubject')?.value || '';
  const grade = document.getElementById('testGrade')?.value || '';
  const topicSelect = document.getElementById('testTopic');
  if (!topicSelect) return;

  let topicPool = [];
  if (subject && STEM_SUBJECT_TOPICS[subject.toLowerCase()]) {
    topicPool = topicPool.concat(STEM_SUBJECT_TOPICS[subject.toLowerCase()]);
  }

  try {
    if (subject) {
      const server = await api.questions.topics({ subject, grade });
      if (Array.isArray(server.topics)) {
        topicPool = topicPool.concat(server.topics.map(t => String(t).toLowerCase()));
      }
    }
  } catch (error) {
    console.warn('Topic fetch fallback to local STEM map only', error);
  }

  setSelectOptions(topicSelect, topicPool, true, 'All Sub Topics');
}

async function initializeStemDropdowns() {
  const subjectOptions = Object.keys(STEM_SUBJECT_TOPICS);
  const testSubject = document.getElementById('testSubject');
  const reportSubject = document.getElementById('reportSubject');

  setSelectOptions(testSubject, subjectOptions, false);
  if (reportSubject) {
    reportSubject.innerHTML = '<option value="">All Subjects</option>';
  }

  if (testSubject && !testSubject.value && subjectOptions.length > 0) {
    testSubject.value = subjectOptions[0];
  }

  await refreshTopicDropdown();

  if (testSubject) {
    testSubject.addEventListener('change', () => {
      refreshTopicDropdown().catch(() => {});
    });
  }

  const gradeSelect = document.getElementById('testGrade');
  if (gradeSelect) {
    gradeSelect.addEventListener('change', () => {
      refreshTopicDropdown().catch(() => {});
    });
  }
}

function setupProfileMenu(session) {
  const userName = document.getElementById('teacherUserName');
  const avatar = document.getElementById('teacherUserAvatar');
  const userInfo = document.getElementById('teacherUserInfo');
  const toggle = document.getElementById('profileMenuToggle');
  const menu = document.querySelector('.profile-menu');

  const name = session.user.name || 'Teacher';
  if (userName) userName.textContent = name;
  if (avatar) avatar.textContent = name.charAt(0).toUpperCase();
  if (userInfo) userInfo.classList.add('hydrated');

  if (toggle && menu) {
    toggle.addEventListener('click', evt => {
      evt.stopPropagation();
      menu.classList.toggle('open');
    });

    document.addEventListener('click', evt => {
      if (!menu.contains(evt.target)) {
        menu.classList.remove('open');
      }
    });
  }

  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', async () => {
      await auth.logout();
    });
  }
}

function destroyChart(id) {
  if (charts[id]) {
    charts[id].destroy();
    delete charts[id];
  }
}

function renderSubjectChart(items) {
  const canvas = document.getElementById('teacherSubjectChart');
  if (!canvas || typeof Chart === 'undefined') return;
  destroyChart('teacherSubjectChart');

  const rows = (Array.isArray(items) ? items : []).map(item => ({
    subject: item.subject || 'Unknown',
    avg_score: Number(item.avg_score || 0),
    total_attempts: Number(item.total_attempts || 0),
  }));

  const labels = rows.map(item => toTitleCase(item.subject));
  const avgData = rows.map(item => item.avg_score);
  const attemptsData = rows.map(item => item.total_attempts);
  const palette = ['#2563eb', '#0891b2', '#16a34a', '#f59e0b', '#db2777', '#7c3aed', '#dc2626', '#0d9488'];

  charts.teacherSubjectChart = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          type: 'bar',
          label: 'Average Score %',
          data: avgData,
          borderWidth: 1,
          backgroundColor: labels.map((_, i) => `${palette[i % palette.length]}CC`),
          borderColor: labels.map((_, i) => palette[i % palette.length]),
          borderRadius: 8,
          yAxisID: 'y',
        },
        {
          type: 'line',
          label: 'Attempts',
          data: attemptsData,
          borderColor: '#111827',
          backgroundColor: 'rgba(17, 24, 39, 0.12)',
          tension: 0.28,
          pointRadius: 3,
          pointHoverRadius: 5,
          borderWidth: 2,
          yAxisID: 'y1',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'bottom' },
        tooltip: {
          callbacks: {
            label(context) {
              if (context.dataset.label === 'Average Score %') {
                return `Average Score: ${Number(context.parsed.y || 0).toFixed(1)}%`;
              }
              return `Attempts: ${Number(context.parsed.y || 0)}`;
            }
          }
        }
      },
      scales: {
        y: { beginAtZero: true, max: 100, ticks: { callback: value => `${value}%` }, title: { display: true, text: 'Average Score' } },
        y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'Attempts' } },
      },
    },
  });
}

function buildGradePerformanceFromStudents(studentRows) {
  const rows = Array.isArray(studentRows) ? studentRows : [];
  const map = new Map();

  rows.forEach(item => {
    const grade = item.grade || 'unknown';
    const attempts = Number(item.total_attempts || 0);
    const avg = Number(item.avg_score || 0);
    const bucket = map.get(grade) || {
      grade,
      total_students: 0,
      active_students: 0,
      total_attempts: 0,
      weighted_sum: 0,
    };
    bucket.total_students += 1;
    bucket.total_attempts += attempts;
    bucket.weighted_sum += avg * attempts;
    if (attempts > 0) bucket.active_students += 1;
    map.set(grade, bucket);
  });

  const order = { elementary: 0, middle: 1, high: 2, college: 3, unknown: 9 };
  return Array.from(map.values())
    .map(item => ({
      grade: item.grade,
      total_students: item.total_students,
      active_students: item.active_students,
      total_attempts: item.total_attempts,
      avg_score: item.total_attempts > 0 ? Number((item.weighted_sum / item.total_attempts).toFixed(2)) : 0,
    }))
    .sort((a, b) => (order[a.grade] ?? 99) - (order[b.grade] ?? 99));
}

function renderGradeLevelChart(items) {
  const canvas = document.getElementById('teacherDifficultyChart');
  if (!canvas || typeof Chart === 'undefined') return;
  destroyChart('teacherDifficultyChart');

  const rows = (Array.isArray(items) ? items : []).map(item => ({
    grade: item.grade || 'unknown',
    avg_score: Number(item.avg_score || 0),
    active_students: Number(item.active_students || 0),
  }));

  charts.teacherDifficultyChart = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: {
      labels: rows.map(item => formatGradeDisplay(item.grade, toTitleCase(item.grade))),
      datasets: [
        {
          type: 'bar',
          label: 'Average Score %',
          data: rows.map(item => item.avg_score),
          backgroundColor: ['#60a5fa', '#34d399', '#f59e0b', '#a78bfa', '#94a3b8'],
          borderColor: ['#2563eb', '#059669', '#d97706', '#7c3aed', '#64748b'],
          borderWidth: 1,
          borderRadius: 8,
          yAxisID: 'y',
        },
        {
          type: 'line',
          label: 'Active Students',
          data: rows.map(item => item.active_students),
          borderColor: '#0f172a',
          backgroundColor: 'rgba(15, 23, 42, 0.12)',
          pointRadius: 4,
          tension: 0.25,
          yAxisID: 'y1',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: true, position: 'bottom' } },
      scales: {
        y: { beginAtZero: true, max: 100, ticks: { callback: value => `${value}%` }, title: { display: true, text: 'Average Score' } },
        y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'Active Students' }, precision: 0 },
      },
    },
  });
}

function renderDifficultyInsights(items) {
  const wrap = document.getElementById('difficultyInsightsList');
  if (!wrap) return;
  const rows = Array.isArray(items) ? items : [];
  if (rows.length === 0) {
    wrap.innerHTML = '<div class="text-muted">No difficulty insight data for selected filters.</div>';
    return;
  }

  const difficultyColor = {
    easy: '#16a34a',
    medium: '#f59e0b',
    hard: '#dc2626',
    unknown: '#64748b',
  };

  wrap.innerHTML = rows.map(item => {
    const difficulty = String(item.difficulty || 'unknown').toLowerCase();
    const color = difficultyColor[difficulty] || difficultyColor.unknown;
    return `
      <div class="difficulty-insight-card" style="--difficulty-accent:${color}">
        <div class="difficulty-title">${toTitleCase(difficulty)}</div>
        <div class="small text-muted">Attempts: ${Number(item.total_attempts || 0)}</div>
        <div class="difficulty-accuracy">${formatPercent(item.accuracy || 0)}</div>
      </div>
    `;
  }).join('');
}

function renderWeakTopics(items) {
  const wrap = document.getElementById('weakTopicsList');
  if (!wrap) return;
  const rows = Array.isArray(items) ? items : [];

  if (rows.length === 0) {
    wrap.innerHTML = '<div class="text-muted">No weak topics identified in the selected period.</div>';
    return;
  }

  wrap.innerHTML = rows.map(item => `
    <div class="weak-topic-item">
      <div><strong>${escapeHtml(item.topic || 'Unknown Topic')}</strong></div>
      <div class="small text-muted">Attempts: ${Number(item.total_attempts || 0)}</div>
      <div class="small">Accuracy: <strong>${Number(item.accuracy || 0).toFixed(1)}%</strong></div>
    </div>
  `).join('');
}

function renderAtRiskStudents(items) {
  const body = document.getElementById('atRiskStudentsBody');
  if (!body) return;

  const rows = Array.isArray(items) ? items : [];
  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="3" class="text-muted">At-risk predictions not available for the selected period.</td></tr>';
    return;
  }

  const safeProb = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };

  const fmtPct = (v) => `${(safeProb(v) * 100).toFixed(1)}%`;

  rows.sort((a, b) => safeProb(b.at_risk_probability) - safeProb(a.at_risk_probability));

  body.innerHTML = rows.map(item => {
    const name = escapeHtml(item.student_name || '-');
    const prob = fmtPct(item.at_risk_probability);

    const top = item.explanation?.top_features?.[0] || null;
    const topText = top
      ? `${escapeHtml(top.feature)} (${Number(top.shap_value || 0).toFixed(3)})`
      : '-';

    return `
      <tr>
        <td>${name}</td>
        <td>${prob}</td>
        <td class="small text-muted">${topText}</td>
      </tr>
    `;
  }).join('');
}

function renderAnalyticsSummary(summary) {
  const data = summary || {};
  const top = data.top_student || null;

  const totalStudents = document.getElementById('analyticsTotalStudents');
  const activeStudents = document.getElementById('analyticsActiveStudents');
  const totalAttempts = document.getElementById('analyticsTotalAttempts');
  const averageScore = document.getElementById('analyticsAverageScore');
  const topStudent = document.getElementById('analyticsTopStudent');

  if (totalStudents) totalStudents.textContent = String(Number(data.total_students || 0));
  if (activeStudents) activeStudents.textContent = String(Number(data.active_students || 0));
  if (totalAttempts) totalAttempts.textContent = String(Number(data.total_attempts || 0));
  if (averageScore) averageScore.textContent = formatPercent(data.average_score || 0);
  if (topStudent) {
    topStudent.textContent = top ? `${top.student_name} (${formatPercent(top.avg_score)})` : '-';
  }
}

function renderStudentAnalyticsTable(items) {
  const body = document.getElementById('analyticsStudentsBody');
  if (!body) return;

  const rows = Array.isArray(items) ? items : [];
  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="6" class="text-muted">No student analytics found for selected filters.</td></tr>';
    return;
  }

  body.innerHTML = rows.map(item => `
    <tr>
      <td>${escapeHtml(item.student_name || '-')}</td>
      <td>${escapeHtml(formatGradeDisplay(item.grade))}</td>
      <td>${Number(item.total_attempts || 0)}</td>
      <td>${formatPercent(item.avg_score || 0)}</td>
      <td>${Number(item.correct_answers || 0)}/${Number(item.total_questions || 0)}</td>
      <td>${escapeHtml(item.best_subject || '-')}</td>
    </tr>
  `).join('');
}

function renderSubjectAnalyticsTable(items) {
  const body = document.getElementById('analyticsSubjectsBody');
  if (!body) return;

  const rows = Array.isArray(items) ? items : [];
  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="4" class="text-muted">No subject analytics found for selected filters.</td></tr>';
    return;
  }

  body.innerHTML = rows.map(item => {
    const top = item.top_student || null;
    const topLabel = top ? `${top.student_name} (${formatPercent(top.avg_score)})` : '-';
    return `
      <tr>
        <td>${escapeHtml(item.subject || '-')}</td>
        <td>${Number(item.total_attempts || 0)}</td>
        <td>${formatPercent(item.avg_score || 0)}</td>
        <td>${escapeHtml(topLabel)}</td>
      </tr>
    `;
  }).join('');
}

async function loadOverview() {
  const data = await api.teacher.getDashboard(30);
  document.getElementById('statStudents').textContent = Number(data.students?.total || 0);
  document.getElementById('statTests').textContent = Number(data.tests?.total || 0);
  document.getElementById('statAttempts').textContent = Number(data.performance?.total_attempts || 0);
  document.getElementById('statAvgScore').textContent = `${Number(data.performance?.average_score || 0).toFixed(1)}%`;
}

async function loadTests() {
  const body = document.getElementById('testsTableBody');
  if (!body) return;

  const result = await api.teacher.getTests();
  const rows = Array.isArray(result.tests) ? result.tests : [];
  teacherCache.tests = rows;
  syncReportSubjectOptions();

  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="8" class="text-muted">No tests created yet.</td></tr>';
    return;
  }

  body.innerHTML = rows.map(item => {
    const isPublished = Boolean(item.is_published);
    const statusClass = isPublished ? 'badge-published' : 'badge-draft';
    const statusText = isPublished ? 'Published' : 'Draft';
    return `
      <tr>
        <td>${escapeHtml(item.title)}</td>
        <td>${escapeHtml(item.subject)}</td>
        <td>${escapeHtml(formatGradeDisplay(item.grade))}</td>
        <td>${escapeHtml(item.difficulty)}</td>
        <td>${Number(item.attempts || 0)}</td>
        <td>${Number(item.average_score || 0).toFixed(1)}%</td>
        <td><span class="badge-soft ${statusClass}">${statusText}</span></td>
        <td class="d-flex gap-2">
          <button class="btn btn-sm btn-outline-secondary" data-action="view" data-id="${item.id}">View</button>
          <button class="btn btn-sm btn-outline-primary" data-action="toggle-publish" data-id="${item.id}" data-value="${isPublished ? '0' : '1'}">${isPublished ? 'Unpublish' : 'Publish'}</button>
          <button class="btn btn-sm btn-outline-danger" data-action="delete" data-id="${item.id}">Delete</button>
        </td>
      </tr>
    `;
  }).join('');
}

async function loadStudents() {
  const body = document.getElementById('studentsTableBody');
  if (!body) return;

  const result = await api.teacher.getStudents();
  const rows = Array.isArray(result.students) ? result.students : [];
  teacherCache.students = rows;

  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="5" class="text-muted">No students found for your scope.</td></tr>';
    return;
  }

  body.innerHTML = rows.map(item => `
    <tr>
      <td>${escapeHtml(item.name)}</td>
      <td>${escapeHtml(item.email)}</td>
      <td>${escapeHtml(formatGradeDisplay(item.grade))}</td>
      <td>${Number(item.attempts || 0)}</td>
      <td>${Number(item.avg_score || 0).toFixed(1)}%</td>
    </tr>
  `).join('');
}

function renderClassroomsTable() {
  const body = document.getElementById('classroomsTableBody');
  if (!body) return;

  const classrooms = teacherCache.classrooms || [];
  const allStudents = getKnownStudents();

  if (classrooms.length === 0) {
    body.innerHTML = '<tr><td colspan="4" class="text-muted">No classrooms created yet.</td></tr>';
    return;
  }

  body.innerHTML = classrooms.map(item => {
    const enrolled = Array.isArray(item.students) ? item.students : [];
    const enrolledIds = new Set(enrolled.map(s => Number(s.id)));
    const candidateRows = allStudents.filter(s => !enrolledIds.has(Number(s.id)));
    const options = candidateRows.map(s => `<option value="${s.id}">${escapeHtml(s.name)} (${escapeHtml(s.email)})</option>`).join('');

    return `
      <tr>
        <td>${escapeHtml(item.name)}</td>
        <td>${escapeHtml(formatGradeDisplay(item.grade))}</td>
        <td>
          <div class="classroom-student-summary">
            <span class="classroom-student-count">${Number(item.student_count || 0)}</span>
            <button class="btn btn-sm btn-outline-secondary classroom-view-students" data-classroom-id="${item.id}">View Students</button>
          </div>
        </td>
        <td>
          <div class="d-flex gap-2">
            <select class="form-control form-control-sm classroom-student-select" data-classroom-id="${item.id}">
              <option value="">Select student</option>
              ${options}
            </select>
            <button class="btn btn-sm btn-outline-primary classroom-add-student" data-classroom-id="${item.id}">Add</button>
            <button class="btn btn-sm btn-outline-secondary classroom-enroll-grade" data-classroom-id="${item.id}">Enroll Grade</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function ensureClassroomStudentsModal() {
  let modal = document.getElementById('classroomStudentsModal');
  if (modal) return modal;

  modal = document.createElement('div');
  modal.id = 'classroomStudentsModal';
  modal.className = 'classroom-students-modal hidden';
  modal.innerHTML = `
    <div class="classroom-students-backdrop" data-close="1"></div>
    <div class="classroom-students-dialog" role="dialog" aria-modal="true" aria-labelledby="classroomStudentsModalTitle">
      <div class="classroom-students-header">
        <h5 id="classroomStudentsModalTitle" class="mb-0">Classroom Students</h5>
        <button type="button" class="btn btn-sm btn-outline-secondary classroom-students-close" data-close="1">Close</button>
      </div>
      <div class="classroom-students-body" id="classroomStudentsModalBody"></div>
    </div>
  `;

  modal.addEventListener('click', (evt) => {
    const shouldClose = evt.target && evt.target.getAttribute && evt.target.getAttribute('data-close') === '1';
    if (shouldClose) {
      modal.classList.add('hidden');
      document.body.classList.remove('modal-open');
    }
  });

  document.body.appendChild(modal);
  return modal;
}

function showClassroomStudentsModal(classroomId) {
  const modal = ensureClassroomStudentsModal();
  const body = document.getElementById('classroomStudentsModalBody');
  const title = document.getElementById('classroomStudentsModalTitle');
  if (!modal || !body || !title) return;

  const classroom = (teacherCache.classrooms || []).find(c => Number(c.id) === Number(classroomId));
  const students = Array.isArray(classroom?.students) ? classroom.students : [];

  title.textContent = classroom?.name ? `${classroom.name} Students` : 'Classroom Students';

  if (students.length === 0) {
    body.innerHTML = '<div class="text-muted">No students enrolled in this classroom yet.</div>';
  } else {
    body.innerHTML = `
      <div class="table-responsive">
        <table class="table table-sm align-middle mb-0">
          <thead><tr><th>Name</th><th>Email</th><th>Grade</th><th>Action</th></tr></thead>
          <tbody>
            ${students.map(s => `
              <tr>
                <td>${escapeHtml(s.name || '-')}</td>
                <td>${escapeHtml(s.email || '-')}</td>
                <td>${escapeHtml(formatGradeDisplay(s.grade))}</td>
                <td><button class="btn btn-sm btn-outline-danger classroom-remove-student" data-classroom-id="${Number(classroomId)}" data-student-id="${Number(s.id)}">Remove</button></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');
}

async function loadClassrooms() {
  const response = await api.teacher.getClassrooms();
  teacherCache.classrooms = Array.isArray(response.classrooms) ? response.classrooms : [];
  renderClassroomsTable();
}

function renderAssignmentsTable() {
  const body = document.getElementById('assignmentsTableBody');
  if (!body) return;

  const rows = teacherCache.assignments || [];
  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="6" class="text-muted">No assignments yet.</td></tr>';
    return;
  }

  body.innerHTML = rows.map(item => {
    const target = item.student?.name
      ? `Student: ${item.student.name}`
      : (item.classroom?.name ? `Classroom: ${item.classroom.name}` : 'Unknown');
    const dueValue = toDateTimeLocalValue(item.due_at);
    return `
      <tr>
        <td>${escapeHtml(item.test?.title || `Test #${item.test_id}`)}</td>
        <td>${escapeHtml(target)}</td>
        <td>${escapeHtml(item.status || 'assigned')}</td>
        <td>${formatDate(item.due_at)}</td>
        <td>
          <div class="d-flex gap-2 align-items-center">
            <input type="datetime-local" class="form-control form-control-sm assignment-due-input" data-id="${item.id}" value="${escapeHtml(dueValue)}" style="min-width: 180px;" />
            <button class="btn btn-sm btn-outline-primary assignment-save-due" data-id="${item.id}">Save</button>
            <button class="btn btn-sm btn-outline-secondary assignment-clear-due" data-id="${item.id}">Clear</button>
          </div>
        </td>
        <td class="d-flex gap-2">
          <button class="btn btn-sm btn-outline-secondary assignment-status" data-id="${item.id}" data-status="reviewed">Mark Reviewed</button>
          <button class="btn btn-sm btn-outline-danger assignment-status" data-id="${item.id}" data-status="cancelled">Cancel</button>
        </td>
      </tr>
    `;
  }).join('');
}

function syncAssignmentFormOptions() {
  const testSelect = document.getElementById('assignmentTestId');
  const classroomSelect = document.getElementById('assignmentClassroomId');
  const studentSelect = document.getElementById('assignmentStudentId');
  const knownStudents = getKnownStudents();

  if (testSelect) {
    const published = (teacherCache.tests || []).filter(t => t.is_published);
    testSelect.innerHTML = '<option value="">Select test</option>' + published.map(t => `<option value="${t.id}">${escapeHtml(t.title)} (${escapeHtml(t.subject)})</option>`).join('');
  }

  if (classroomSelect) {
    classroomSelect.innerHTML = '<option value="">No classroom target</option>' + (teacherCache.classrooms || []).map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
  }

  if (studentSelect) {
    studentSelect.innerHTML = '<option value="">No direct student target</option>' + knownStudents.map(s => `<option value="${s.id}">${escapeHtml(s.name)} (${escapeHtml(s.email)})</option>`).join('');
  }
}

async function loadAssignments() {
  const response = await api.teacher.getAssignments();
  teacherCache.assignments = Array.isArray(response.assignments) ? response.assignments : [];
  renderAssignmentsTable();
}

async function loadReports() {
  const body = document.getElementById('teacherReportsBody');
  if (!body) return;

  const subject = document.getElementById('reportSubject')?.value?.trim() || '';
  const days = Number(document.getElementById('reportDays')?.value || 30);
  const limit = Number(document.getElementById('reportLimit')?.value || 100);

  const result = await api.teacher.getReports({ subject, days, limit });
  const rows = Array.isArray(result.items) ? result.items : [];
  teacherCache.reportRows = rows;
  syncReportSubjectOptions();

  if (rows.length === 0) {
    body.innerHTML = '<tr><td colspan="6" class="text-muted">No report rows for selected filters.</td></tr>';
    return;
  }

  body.innerHTML = rows.map(item => `
    <tr>
      <td>${escapeHtml(item.student_name)}</td>
      <td>${escapeHtml(item.subject)}</td>
      <td>${Number(item.score_pct || 0).toFixed(1)}%</td>
      <td>${Number(item.correct_answers || 0)}/${Number(item.total_questions || 0)}</td>
      <td>${escapeHtml(item.status || '-')}</td>
      <td>${formatDate(item.test_date)}</td>
    </tr>
  `).join('');
}

async function loadAnalytics() {
  const days = Number(document.getElementById('analyticsDays')?.value || document.getElementById('reportDays')?.value || 30);
  const grade = document.getElementById('analyticsGrade')?.value || '';
  const result = await api.teacher.getAnalytics({ days, grade });
  const gradeRows = Array.isArray(result.grade_performance) && result.grade_performance.length > 0
    ? result.grade_performance
    : buildGradePerformanceFromStudents(result.student_performance || []);

  renderAnalyticsSummary(result.summary || {});
  renderStudentAnalyticsTable(result.student_performance || []);
  renderSubjectAnalyticsTable(result.subject_performance || []);
  renderSubjectChart(result.subject_performance || []);
  renderGradeLevelChart(gradeRows);
  renderDifficultyInsights(result.difficulty_performance || []);
  renderWeakTopics(result.weak_topics || []);
  renderAtRiskStudents(result.at_risk_students || []);
}

function bindTestActions() {
  const body = document.getElementById('testsTableBody');
  if (!body) return;

  body.addEventListener('click', async evt => {
    const btn = evt.target.closest('button[data-action]');
    if (!btn) return;

    const action = btn.dataset.action;
    const testId = Number(btn.dataset.id);
    if (!testId) return;

    try {
      if (action === 'view') {
        const detail = await api.teacher.getTest(testId);
        const panel = document.getElementById('testDetailPanel');
        if (panel) {
          const questions = Array.isArray(detail.questions) ? detail.questions : [];
          panel.classList.remove('hidden');
          panel.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-2">
              <h5 class="mb-0">${escapeHtml(detail.title)} Questions</h5>
              <button class="btn btn-sm btn-primary" id="saveTestQuestionsBtn" data-test-id="${testId}">Update Questions</button>
            </div>
            <div class="small text-muted mb-2">Drag by the serial card to reorder. Edit question, points, answers, and correct answer. Then click Update Questions.</div>
            <div class="test-question-editor-list d-flex flex-column gap-3">
              ${questions.map((q, idx) => {
                const opts = Array.isArray(q.options) ? q.options : [];
                const safeOpts = [0, 1, 2, 3].map(i => escapeHtml(opts[i] || ''));
                const orderVal = Number(q.order || (idx + 1));
                const pointsVal = Number(q.points || 1);
                const correctVal = Number(q.correct_index || 0);
                const correctAnswerText = safeOpts[correctVal] || '-';
                return `
                  <article class="test-question-editor-row" data-question-id="${q.id}">
                    <div class="question-card-head">
                      <button type="button" class="question-serial-card" title="Drag to reorder">
                        <span class="question-serial-prefix">Q</span><span class="question-serial-number">${orderVal}</span>
                      </button>
                      <div class="question-points-wrap">
                        <label class="form-label small mb-1">Points</label>
                        <input type="number" class="form-control form-control-sm q-points" min="1" value="${pointsVal}" />
                      </div>
                    </div>
                    <input type="hidden" class="q-order" value="${orderVal}" />
                    <div class="question-main-field">
                      <label class="form-label small mb-1">Question</label>
                      <input type="text" class="form-control q-text" value="${escapeHtml(q.text || '')}" />
                    </div>
                    <div class="question-options-list">
                      <label class="form-label small mb-1">Answers</label>
                      <div class="option-row"><span class="option-label">A</span><input type="text" class="form-control form-control-sm q-opt-0" placeholder="Option 1" value="${safeOpts[0]}" /></div>
                      <div class="option-row"><span class="option-label">B</span><input type="text" class="form-control form-control-sm q-opt-1" placeholder="Option 2" value="${safeOpts[1]}" /></div>
                      <div class="option-row"><span class="option-label">C</span><input type="text" class="form-control form-control-sm q-opt-2" placeholder="Option 3" value="${safeOpts[2]}" /></div>
                      <div class="option-row"><span class="option-label">D</span><input type="text" class="form-control form-control-sm q-opt-3" placeholder="Option 4" value="${safeOpts[3]}" /></div>
                    </div>
                    <div class="question-correct-row">
                      <div class="question-correct-select">
                        <label class="form-label small mb-1">Correct Answer</label>
                        <select class="form-control form-control-sm q-correct">
                          <option value="0" ${correctVal === 0 ? 'selected' : ''}>Option 1</option>
                          <option value="1" ${correctVal === 1 ? 'selected' : ''}>Option 2</option>
                          <option value="2" ${correctVal === 2 ? 'selected' : ''}>Option 3</option>
                          <option value="3" ${correctVal === 3 ? 'selected' : ''}>Option 4</option>
                        </select>
                      </div>
                      <div class="question-correct-text">Current correct: <strong>${correctAnswerText}</strong></div>
                    </div>
                  </article>
                `;
              }).join('')}
            </div>
          `;

          enableQuestionCardDragAndDrop(panel);

          const saveBtn = panel.querySelector('#saveTestQuestionsBtn');
          if (saveBtn) {
            saveBtn.addEventListener('click', async () => {
              try {
                const questionPayload = collectEditableQuestionsFromPanel(panel);
                if (questionPayload.length === 0) {
                  utils.showNotification('No questions to update.', 'warning');
                  return;
                }

                await api.teacher.updateTest(testId, { questions: questionPayload });
                utils.showNotification('Test questions updated successfully.', 'success');
                await loadTests();
              } catch (error) {
                console.error(error);
                utils.showNotification(error.message || 'Failed to update test questions', 'error');
              }
            });
          }
        }
      }

      if (action === 'toggle-publish') {
        const publish = btn.dataset.value === '1';
        await api.teacher.updateTest(testId, { is_published: publish });
        utils.showNotification(`Test ${publish ? 'published' : 'unpublished'} successfully.`, 'success');
        await loadTests();
      }

      if (action === 'delete') {
        if (!window.confirm('Delete this test? This action cannot be undone.')) return;
        await api.teacher.deleteTest(testId);
        utils.showNotification('Test deleted successfully.', 'success');
        await loadTests();
      }
    } catch (error) {
      console.error(error);
      utils.showNotification(error.message || 'Action failed', 'error');
    }
  });
}

function collectBuilderPayload() {
  const seedRaw = document.getElementById('testSeed')?.value?.trim();
  const seed = seedRaw ? Number(seedRaw) : null;

  const payload = {
    title: document.getElementById('testTitle')?.value?.trim(),
    description: document.getElementById('testDescription')?.value?.trim(),
    subject: document.getElementById('testSubject')?.value?.trim(),
    grade: document.getElementById('testGrade')?.value,
    difficulty: document.getElementById('testDifficulty')?.value,
    question_count: Number(document.getElementById('testCount')?.value || 10),
    time_limit: Number(document.getElementById('testTimeLimit')?.value || 30),
    topic: document.getElementById('testTopic')?.value?.trim(),
  };

  if (!Number.isNaN(seed) && seed !== null) {
    payload.seed = seed;
  }

  return payload;
}

async function createTest(event) {
  event.preventDefault();
  const button = document.getElementById('createTestBtn');
  setBusy(button, 'Creating');
  try {
    const payload = collectBuilderPayload();
    if (!payload.title || !payload.subject) {
      utils.showNotification('Title and subject are required.', 'warning');
      return;
    }

    const result = await api.teacher.createTest(payload);
    const diagnostics = formatGenerationDiagnostics(result?.generation_status);
    if (result && result.warning) {
      utils.showNotification(result.warning, 'warning');
    } else {
      utils.showNotification('Test created successfully.', 'success');
    }

    if (diagnostics.summary || diagnostics.error) {
      const endpointSuffix = diagnostics.endpoint ? ` | ${diagnostics.endpoint}` : '';
      const errorSuffix = diagnostics.error ? ` | ${diagnostics.error}` : '';
      utils.showNotification(`Generation diagnostics: ${diagnostics.summary}${endpointSuffix}${errorSuffix}`, 'info');
    }

    await Promise.all([loadOverview(), loadTests()]);
    syncAssignmentFormOptions();
  } catch (error) {
    console.error(error);
    const diagnostics = formatGenerationDiagnostics(error?.payload?.generation_status);
    if (diagnostics.summary || diagnostics.error) {
      utils.showNotification(`Generation diagnostics: ${diagnostics.summary}${diagnostics.error ? ` | ${diagnostics.error}` : ''}`, 'warning');
    }
    utils.showNotification(error.message || 'Failed to create test', 'error');
  } finally {
    clearBusy(button);
  }
}

async function loadAllTeacherData() {
  const results = await Promise.allSettled([
    loadOverview(),
    loadTests(),
    loadStudents(),
    loadClassrooms(),
    loadAssignments(),
    loadReports(),
    loadAnalytics(),
  ]);

  const failures = results.filter(item => item.status === 'rejected');
  if (failures.length > 0) {
    console.warn('Teacher dashboard partial load failures:', failures);
    utils.showNotification('Some sections could not be loaded. You can retry from each section.', 'warning');
  }

  syncAssignmentFormOptions();
}

function bindEvents() {
  const createForm = document.getElementById('createTestForm');
  if (createForm) {
    createForm.addEventListener('submit', createTest);
  }

  const refreshReportsBtn = document.getElementById('refreshReportsBtn');
  if (refreshReportsBtn) {
    refreshReportsBtn.addEventListener('click', async () => {
      await Promise.all([loadReports(), loadAnalytics()]);
    });
  }

  const refreshAnalyticsBtn = document.getElementById('refreshAnalyticsBtn');
  if (refreshAnalyticsBtn) {
    refreshAnalyticsBtn.addEventListener('click', async () => {
      await loadAnalytics();
    });
  }

  const classroomForm = document.getElementById('classroomForm');
  if (classroomForm) {
    classroomForm.addEventListener('submit', async evt => {
      evt.preventDefault();
      const btn = document.getElementById('createClassroomBtn');
      setBusy(btn, 'Creating');
      try {
        const payload = {
          name: document.getElementById('classroomName')?.value?.trim(),
          grade: document.getElementById('classroomGrade')?.value,
          auto_enroll_students: Boolean(document.getElementById('autoEnrollGradeStudents')?.checked),
        };
        if (!payload.name) {
          utils.showNotification('Classroom name is required.', 'warning');
          return;
        }
        const result = await api.teacher.createClassroom(payload);
        if (result && result.auto_enroll_students) {
          const enrolledCount = Number(result.enrolled_count || 0);
          utils.showNotification(`Classroom created. ${enrolledCount} student(s) enrolled automatically.`, 'success');
        } else {
          utils.showNotification('Classroom created.', 'success');
        }
        classroomForm.reset();
        const autoEnroll = document.getElementById('autoEnrollGradeStudents');
        if (autoEnroll) autoEnroll.checked = true;
        await Promise.all([loadClassrooms(), loadStudents()]);
        syncAssignmentFormOptions();
      } catch (error) {
        console.error(error);
        utils.showNotification(error.message || 'Failed to create classroom', 'error');
      } finally {
        clearBusy(btn);
      }
    });
  }

  const classroomsBody = document.getElementById('classroomsTableBody');
  if (classroomsBody) {
    classroomsBody.addEventListener('click', async evt => {
      const viewBtn = evt.target.closest('.classroom-view-students');
      if (viewBtn) {
        const classroomId = Number(viewBtn.dataset.classroomId);
        if (!classroomId) return;
        showClassroomStudentsModal(classroomId);
        return;
      }

      const enrollGradeBtn = evt.target.closest('.classroom-enroll-grade');
      if (enrollGradeBtn) {
        const classroomId = Number(enrollGradeBtn.dataset.classroomId);
        if (!classroomId) return;

        try {
          const result = await api.teacher.enrollClassroomByGrade(classroomId);
          utils.showNotification(`Enrolled ${Number(result.enrolled_count || 0)} student(s) by grade.`, 'success');
          await Promise.all([loadClassrooms(), loadStudents()]);
          syncAssignmentFormOptions();
        } catch (error) {
          console.error(error);
          utils.showNotification(error.message || 'Failed to enroll students by grade', 'error');
        }
        return;
      }

      const removeBtn = evt.target.closest('.classroom-remove-student');
      if (removeBtn) {
        const classroomId = Number(removeBtn.dataset.classroomId);
        const studentId = Number(removeBtn.dataset.studentId);
        if (!classroomId || !studentId) return;

        try {
          await api.teacher.removeStudentFromClassroom(classroomId, studentId);
          utils.showNotification('Student removed from classroom.', 'success');
          await Promise.all([loadClassrooms(), loadStudents()]);
          syncAssignmentFormOptions();
        } catch (error) {
          console.error(error);
          utils.showNotification(error.message || 'Failed to remove student', 'error');
        }
        return;
      }

      const addBtn = evt.target.closest('.classroom-add-student');
      if (!addBtn) return;

      const classroomId = Number(addBtn.dataset.classroomId);
      const select = classroomsBody.querySelector(`.classroom-student-select[data-classroom-id="${classroomId}"]`);
      const studentId = Number(select?.value || 0);
      if (!classroomId || !studentId) {
        utils.showNotification('Select a student first.', 'warning');
        return;
      }

      try {
        await api.teacher.addStudentToClassroom(classroomId, studentId);
        utils.showNotification('Student added to classroom.', 'success');
        await Promise.all([loadClassrooms(), loadStudents()]);
        syncAssignmentFormOptions();
      } catch (error) {
        console.error(error);
        utils.showNotification(error.message || 'Failed to add student', 'error');
      }
    });
  }

  const modal = ensureClassroomStudentsModal();
  if (modal) {
    modal.addEventListener('click', async evt => {
      const removeBtn = evt.target.closest('.classroom-remove-student');
      if (!removeBtn) return;

      const classroomId = Number(removeBtn.dataset.classroomId);
      const studentId = Number(removeBtn.dataset.studentId);
      if (!classroomId || !studentId) return;

      try {
        await api.teacher.removeStudentFromClassroom(classroomId, studentId);
        utils.showNotification('Student removed from classroom.', 'success');
        await Promise.all([loadClassrooms(), loadStudents()]);
        syncAssignmentFormOptions();
        showClassroomStudentsModal(classroomId);
      } catch (error) {
        console.error(error);
        utils.showNotification(error.message || 'Failed to remove student', 'error');
      }
    });
  }

  const assignmentForm = document.getElementById('assignmentForm');
  if (assignmentForm) {
    assignmentForm.addEventListener('submit', async evt => {
      evt.preventDefault();
      const btn = document.getElementById('createAssignmentBtn');
      setBusy(btn, 'Assigning');
      try {
        const payload = {
          test_id: Number(document.getElementById('assignmentTestId')?.value || 0),
          classroom_id: Number(document.getElementById('assignmentClassroomId')?.value || 0) || null,
          student_id: Number(document.getElementById('assignmentStudentId')?.value || 0) || null,
          due_at: document.getElementById('assignmentDueAt')?.value ? new Date(document.getElementById('assignmentDueAt').value).toISOString() : null,
          notes: document.getElementById('assignmentNotes')?.value?.trim() || null,
          is_mandatory: document.getElementById('assignmentMandatory')?.value === '1',
          allow_late: document.getElementById('assignmentAllowLate')?.value === '1',
        };

        if (!payload.test_id) {
          utils.showNotification('Select a test to assign.', 'warning');
          return;
        }
        if (!payload.classroom_id && !payload.student_id) {
          utils.showNotification('Select exactly one target: classroom or student.', 'warning');
          return;
        }
        if (payload.classroom_id && payload.student_id) {
          utils.showNotification('Choose either classroom or student target, not both.', 'warning');
          return;
        }

        await api.teacher.createAssignment(payload);
        utils.showNotification('Assignment created successfully.', 'success');
        assignmentForm.reset();
        await loadAssignments();
      } catch (error) {
        console.error(error);
        utils.showNotification(error.message || 'Failed to create assignment', 'error');
      } finally {
        clearBusy(btn);
      }
    });
  }

  const assignmentsBody = document.getElementById('assignmentsTableBody');
  if (assignmentsBody) {
    assignmentsBody.addEventListener('click', async evt => {
      const saveDueBtn = evt.target.closest('.assignment-save-due');
      if (saveDueBtn) {
        const assignmentId = Number(saveDueBtn.dataset.id);
        if (!assignmentId) return;

        const input = assignmentsBody.querySelector(`.assignment-due-input[data-id="${assignmentId}"]`);
        const raw = String(input?.value || '').trim();
        const dueIso = raw ? new Date(raw).toISOString() : null;

        try {
          await api.teacher.updateAssignment(assignmentId, { due_at: dueIso });
          utils.showNotification('Assignment due date/time updated.', 'success');
          await loadAssignments();
        } catch (error) {
          console.error(error);
          utils.showNotification(error.message || 'Failed to update due date/time', 'error');
        }
        return;
      }

      const clearDueBtn = evt.target.closest('.assignment-clear-due');
      if (clearDueBtn) {
        const assignmentId = Number(clearDueBtn.dataset.id);
        if (!assignmentId) return;

        try {
          await api.teacher.updateAssignment(assignmentId, { due_at: null });
          utils.showNotification('Assignment due date/time cleared.', 'success');
          await loadAssignments();
        } catch (error) {
          console.error(error);
          utils.showNotification(error.message || 'Failed to clear due date/time', 'error');
        }
        return;
      }

      const statusBtn = evt.target.closest('.assignment-status');
      if (!statusBtn) return;
      const assignmentId = Number(statusBtn.dataset.id);
      const status = statusBtn.dataset.status;
      if (!assignmentId || !status) return;

      try {
        await api.teacher.updateAssignment(assignmentId, { status });
        utils.showNotification('Assignment status updated.', 'success');
        await loadAssignments();
      } catch (error) {
        console.error(error);
        utils.showNotification(error.message || 'Failed to update assignment', 'error');
      }
    });
  }

  bindTestActions();
  bindSidebarNav();
}

async function initTeacherDashboard() {
  const session = ensureTeacherSession();
  if (!session) return;

  setupProfileMenu(session);
  await initializeStemDropdowns();
  bindEvents();

  try {
    await loadAllTeacherData();
  } catch (error) {
    console.error(error);
    utils.showNotification(error.message || 'Failed to load teacher dashboard', 'error');
  }
}

document.addEventListener('DOMContentLoaded', initTeacherDashboard);
