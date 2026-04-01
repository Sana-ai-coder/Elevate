// API module for backend communication
import { config } from './config.js';

export const api = {
  buildFriendlyApiError(endpoint, method, status, serverMessage = '') {
    const route = String(endpoint || '').toLowerCase();
    const verb = String(method || 'GET').toUpperCase();

    if (route.includes('/auth/login')) {
      if (status === 401) return 'We could not sign you in. Please check your email and password and try again.';
      return 'Sign-in is unavailable right now. Please try again.';
    }

    if (route.includes('/auth/signup')) {
      if (status === 409) return 'This email is already registered. Please sign in or use a different email.';
      return 'We could not create your account right now. Please try again.';
    }

    if (route.includes('/teacher/tests') && verb === 'POST') {
      return 'We could not create the test right now. Please try again.';
    }

    if (route.includes('/teacher/question-bank/generate')) {
      return 'We could not prepare questions right now. Please try again.';
    }

    if (status === 400) return 'We could not process that request. Please check your details and try again.';
    if (status === 401) return 'Your session has expired. Please sign in again.';
    if (status === 403) return 'You do not have permission to do that action.';
    if (status === 404) return 'The requested item could not be found.';
    if (status === 409) return 'A conflicting record already exists. Please review your input and try again.';
    if (status === 429) return 'Too many requests right now. Please wait a moment and try again.';
    if ([500, 502, 503, 504].includes(Number(status))) {
      return 'Something went wrong on our side. Please try again in a moment.';
    }

    if (serverMessage && String(serverMessage).trim()) {
      return 'We could not complete this request right now. Please try again.';
    }

    return 'We could not complete your request. Please try again.';
  },

  buildFriendlyNetworkError() {
    return 'We could not connect to the server right now. Please check your connection and try again.';
  },

  // Get stored JWT token from both localStorage and sessionStorage
  getToken() {
    try {
      // Try localStorage first (Remember Me = checked)
      let session = localStorage.getItem('elevate_user_session');
      let storage = 'localStorage';
      
      // If not in localStorage, try sessionStorage (Remember Me = unchecked)
      if (!session) {
        session = sessionStorage.getItem('elevate_user_session');
        storage = 'sessionStorage';
      }
      
      if (session) {
        const parsed = JSON.parse(session);
        console.log(`Retrieved token from ${storage}:`, parsed.token ? 'Token exists' : 'No token');
        return parsed.token || null;
      } else {
        console.log('No session found in localStorage or sessionStorage');
      }
    } catch (e) {
      console.warn('Failed to get token', e);
    }
    return null;
  },

  // Base request function with JWT support
  async request(endpoint, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    let url = `${config.API_BASE_URL}${endpoint}`;
    const token = this.getToken();

    // Cache-bust GET requests without adding custom headers that can break CORS preflight.
    if (method === 'GET') {
      const separator = url.includes('?') ? '&' : '?';
      url = `${url}${separator}_ts=${Date.now()}`;
    }
    
    const defaultOptions = {
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
      },
      cache: 'no-store',
      ...options,
    };

    // Merge headers if provided in options
    if (options.headers) {
      defaultOptions.headers = {
        ...defaultOptions.headers,
        ...options.headers,
      };
    }

    try {
      const response = await fetch(url, defaultOptions);
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        // Handle 401 - token expired or invalid
        if (response.status === 401) {
          // Clear both storage locations and redirect to login
          localStorage.removeItem('elevate_user_session');
          sessionStorage.removeItem('elevate_user_session');
          const currentPath = window.location.pathname;
          if (!currentPath.endsWith('index.html') && currentPath !== '/') {
            window.location.href = 'index.html';
          }
        }
        
        const serverMessage = data.error || data.message || '';
        const userMessage = this.buildFriendlyApiError(endpoint, method, response.status, serverMessage);
        const requestError = new Error(userMessage);
        requestError.status = response.status;
        requestError.payload = data;
        requestError.userMessage = userMessage;
        requestError.serverMessage = serverMessage;
        throw requestError;
      }

      return data;
    } catch (error) {
      if (!error.userMessage) {
        const fallback = this.buildFriendlyNetworkError();
        error.serverMessage = error.message || '';
        error.userMessage = fallback;
        error.message = fallback;
      }
      console.error('API Error:', error);
      throw error;
    }
  },

  // Auth endpoints -> real Flask backend
  auth: {
    async login(email, password) {
      const data = await api.request('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      return {
        success: true,
        user: data.user,
        token: data.token,
      };
    },

    async signup(name, email, password, grade, role = 'student') {
      const data = await api.request('/auth/signup', {
        method: 'POST',
        body: JSON.stringify({ name, email, password, grade, role }),
      });
      return {
        success: true,
        user: data.user,
        token: data.token,
      };
    },

    async logout() {
      try {
        await api.request('/auth/logout', {
          method: 'POST',
        });
      } catch (error) {
        console.warn('Logout API call failed, but clearing local session anyway');
      }
      return { success: true };
    },

    async getProfile() {
      const data = await api.request('/auth/me', {
        method: 'GET',
      });
      return {
        success: true,
        user: data.user,
      };
    },
  },

  // Question endpoints
  questions: {
    async list(params = {}) {
      const queryParams = new URLSearchParams();
      if (params.grade) queryParams.append('grade', params.grade);
      if (params.subject) queryParams.append('subject', params.subject);
      if (params.difficulty) queryParams.append('difficulty', params.difficulty);
      if (params.exclude_answered !== undefined) queryParams.append('exclude_answered', params.exclude_answered);
      if (params.limit) queryParams.append('limit', params.limit);
      if (params.offset) queryParams.append('offset', params.offset);
      
      const queryString = queryParams.toString();
      const endpoint = queryString ? `/questions?${queryString}` : '/questions';
      
      return await api.request(endpoint, { method: 'GET' });
    },

    async get(id) {
      return await api.request(`/questions/${id}`, { method: 'GET' });
    },

    async submit(questionId, selectedIndex, timeSpent, emotion = null) {
      return await api.request(`/questions/${questionId}/submit`, {
        method: 'POST',
        body: JSON.stringify({
          selected_index: selectedIndex,
          time_spent: timeSpent,
          emotion: emotion
        }),
      });
    },

    async generate(params = {}) {
      const query = new URLSearchParams();
      if (params.grade) query.append('grade', params.grade);
      if (params.subject) query.append('subject', params.subject);
      if (params.topic) query.append('topic', params.topic);
      if (params.difficulty) query.append('difficulty', params.difficulty);
      if (params.count) query.append('count', params.count);
      if (params.exclude_answered !== undefined) query.append('exclude_answered', params.exclude_answered);
      const endpoint = `/questions/generate?${query.toString()}`;
      return await api.request(endpoint, { method: 'GET' });
    },

    async topics(params = {}) {
      const query = new URLSearchParams();
      if (params.grade) query.append('grade', params.grade);
      if (params.subject) query.append('subject', params.subject);
      const endpoint = `/questions/topics?${query.toString()}`;
      return await api.request(endpoint, { method: 'GET' });
    }
  },

  // Progress endpoints
  progress: {
    async get() {
      return await api.request('/progress', { method: 'GET' });
    },

    async getSubject(subject) {
      return await api.request(`/progress/${subject}`, { method: 'GET' });
    },

    async getDashboardStats() {
      return await api.request('/progress/stats/dashboard', { method: 'GET' });
    }
  },

  // Emotion endpoints
  emotions: {
    async log(emotion, confidence, context = null) {
      return await api.request('/emotions', {
        method: 'POST',
        body: JSON.stringify({
          emotion: emotion,
          confidence: confidence,
          context: context
        }),
      });
    },

    async getHistory(params = {}) {
      const queryParams = new URLSearchParams();
      if (params.limit) queryParams.append('limit', params.limit);
      if (params.offset) queryParams.append('offset', params.offset);
      if (params.days) queryParams.append('days', params.days);
      if (params.context) queryParams.append('context', params.context);
      
      const queryString = queryParams.toString();
      const endpoint = queryString ? `/emotions/history?${queryString}` : '/emotions/history';
      
      return await api.request(endpoint, { method: 'GET' });
    },

    async getSummary(days = 7) {
      return await api.request(`/emotions/summary?days=${days}`, { method: 'GET' });
    },

    async getTimeline(days = 7, groupBy = 'day') {
      return await api.request(`/emotions/timeline?days=${days}&group_by=${groupBy}`, { method: 'GET' });
    }
  },

  // Reports endpoints
  reports: {
    async getSummary(days = 30) {
      return await api.request(`/reports/summary?days=${days}`, { method: 'GET' });
    },

    async getSubjects(days = 30) {
      return await api.request(`/reports/subjects?days=${days}`, { method: 'GET' });
    },

    async getEmotions(days = 30) {
      return await api.request(`/reports/emotions?days=${days}`, { method: 'GET' });
    },

    async getTimeline(days = 30) {
      return await api.request(`/reports/timeline?days=${days}`, { method: 'GET' });
    },

    async getIntegrity(days = 30) {
      return await api.request(`/reports/integrity?days=${days}`, { method: 'GET' });
    }
  },

  // Settings endpoints
  settings: {
    async get() {
      return await api.request('/settings', { method: 'GET' });
    },

    async update(settings) {
      return await api.request('/settings', {
        method: 'PUT',
        body: JSON.stringify(settings || {}),
      });
    }
  },

  // Teacher endpoints
  teacher: {
    async getDashboard(days = 30) {
      return await api.request(`/teacher/dashboard?days=${days}`, { method: 'GET' });
    },

    async getTests() {
      return await api.request('/teacher/tests', { method: 'GET' });
    },

    async getTest(testId) {
      return await api.request(`/teacher/tests/${testId}`, { method: 'GET' });
    },

    async createTest(payload) {
      return await api.request('/teacher/tests', {
        method: 'POST',
        body: JSON.stringify(payload || {}),
      });
    },

    async updateTest(testId, payload) {
      return await api.request(`/teacher/tests/${testId}`, {
        method: 'PUT',
        body: JSON.stringify(payload || {}),
      });
    },

    async deleteTest(testId) {
      return await api.request(`/teacher/tests/${testId}`, {
        method: 'DELETE',
      });
    },

    async getStudents(grade = '') {
      const query = grade ? `?grade=${encodeURIComponent(grade)}` : '';
      return await api.request(`/teacher/students${query}`, { method: 'GET' });
    },

    async getClassrooms() {
      return await api.request('/teacher/classrooms', { method: 'GET' });
    },

    async createClassroom(payload) {
      return await api.request('/teacher/classrooms', {
        method: 'POST',
        body: JSON.stringify(payload || {}),
      });
    },

    async enrollClassroomByGrade(classroomId) {
      return await api.request(`/teacher/classrooms/${classroomId}/enroll-grade`, {
        method: 'POST',
      });
    },

    async addStudentToClassroom(classroomId, studentId) {
      return await api.request(`/teacher/classrooms/${classroomId}/students`, {
        method: 'POST',
        body: JSON.stringify({ student_id: studentId }),
      });
    },

    async removeStudentFromClassroom(classroomId, studentId) {
      return await api.request(`/teacher/classrooms/${classroomId}/students/${studentId}`, {
        method: 'DELETE',
      });
    },

    async getAssignments(limit = 200) {
      return await api.request(`/teacher/assignments?limit=${limit}`, { method: 'GET' });
    },

    async createAssignment(payload) {
      return await api.request('/teacher/assignments', {
        method: 'POST',
        body: JSON.stringify(payload || {}),
      });
    },

    async updateAssignment(assignmentId, payload) {
      return await api.request(`/teacher/assignments/${assignmentId}`, {
        method: 'PATCH',
        body: JSON.stringify(payload || {}),
      });
    },

    async getReports(params = {}) {
      const query = new URLSearchParams();
      if (params.subject) query.append('subject', params.subject);
      if (params.grade) query.append('grade', params.grade);
      if (params.days) query.append('days', String(params.days));
      if (params.limit) query.append('limit', String(params.limit));
      const qs = query.toString();
      return await api.request(`/teacher/reports${qs ? `?${qs}` : ''}`, { method: 'GET' });
    },

    async getAnalytics(params = {}) {
      const query = new URLSearchParams();
      if (params.grade) query.append('grade', params.grade);
      if (params.days) query.append('days', String(params.days));
      const qs = query.toString();
      return await api.request(`/teacher/analytics${qs ? `?${qs}` : ''}`, { method: 'GET' });
    },

    async generateQuestionBank(payload) {
      return await api.request('/teacher/question-bank/generate', {
        method: 'POST',
        body: JSON.stringify(payload || {}),
      });
    }
  },

  student: {
    async getAssignedTests() {
      return await api.request('/student/assigned-tests', { method: 'GET' });
    },

    async getAvailableTests() {
      return await api.request('/student/tests', { method: 'GET' });
    },

    async startTest(testId) {
      return await api.request(`/student/tests/${testId}/start`, { method: 'POST' });
    },

    async getTestQuestions(testId) {
      return await api.request(`/student/tests/${testId}/questions`, { method: 'GET' });
    },

    async submitTestAnswer(testId, payload) {
      return await api.request(`/student/tests/${testId}/answer`, {
        method: 'POST',
        body: JSON.stringify(payload || {}),
      });
    },

    async finishTest(testId) {
      return await api.request(`/student/tests/${testId}/finish`, { method: 'POST' });
    }
  }
};
