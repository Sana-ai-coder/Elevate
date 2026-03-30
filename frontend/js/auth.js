import { state, updateState } from './state.js';
import { utils } from './utils.js';
import { api } from './api.js';

const SESSION_KEY = 'elevate_user_session';

export const auth = {
  // ------- Session helpers (supports both localStorage and sessionStorage) -------

  loadSession() {
    try {
      // Try localStorage first (Remember Me = checked)
      let raw = localStorage.getItem(SESSION_KEY);
      let storage = 'localStorage';
      
      // If not in localStorage, try sessionStorage (Remember Me = unchecked)
      if (!raw) {
        raw = sessionStorage.getItem(SESSION_KEY);
        storage = 'sessionStorage';
      }
      
      if (!raw) return null;
      
      const session = JSON.parse(raw);
      if (session && session.user && session.token) {
        console.log(`✅ Session loaded from ${storage}`);
        updateState({ currentUser: session.user });
        return session;
      }
    } catch (e) {
      console.warn('Failed to parse session', e);
      localStorage.removeItem(SESSION_KEY);
      sessionStorage.removeItem(SESSION_KEY);
    }
    return null;
  },

  saveSession(user, token, rememberMe = false) {
    const session = { user, token };
    
    if (rememberMe) {
      // Remember Me checked: use localStorage (persists across browser restarts)
      localStorage.setItem(SESSION_KEY, JSON.stringify(session));
      sessionStorage.removeItem(SESSION_KEY); // Clean up sessionStorage
      console.log('💾 Session saved to localStorage (Remember Me: ON)');
    } else {
      // Remember Me unchecked: use sessionStorage (only during browser session)
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
      localStorage.removeItem(SESSION_KEY); // Clean up localStorage
      console.log('⏱️ Session saved to sessionStorage (Remember Me: OFF)');
    }
    
    updateState({ currentUser: user });
  },

  clearSession() {
    localStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(SESSION_KEY);
    updateState({ currentUser: null });
  },

  // ------- Auth flows (via backend API) -------

  async login(email, password, rememberMe = false) {
    try {
      if (!email || !password) {
        return { success: false, error: 'Please enter both email and password' };
      }

      const result = await api.auth.login(email, password);
      console.log('Login result:', { hasUser: !!result.user, hasToken: !!result.token, rememberMe });
      this.saveSession(result.user, result.token, rememberMe);
      return { success: true };
    } catch (error) {
      console.error('Login error:', error);
      return { success: false, error: error.message || 'Login failed. Please check console.' };
    }
  },

  async signup(name, email, password, grade, role = 'student') {
    try {
      if (!name || !email || !password || !grade) {
        return { success: false, error: 'All fields are required' };
      }

      // Basic client-side validation
      if (password.length < 8) {
        return { success: false, error: 'Password must be at least 8 characters long' };
      }

      const result = await api.auth.signup(name, email, password, grade, role);
      this.saveSession(result.user, result.token);
      return { success: true };
    } catch (error) {
      console.error('Signup error:', error);
      
      // Handle detailed validation errors from backend
      let errorMessage = error.message || 'Signup failed.';
      
      // Try to parse error response for details
      try {
        const errorData = JSON.parse(error.message);
        if (errorData.details && Array.isArray(errorData.details)) {
          errorMessage = errorData.details.join(', ');
        }
      } catch (e) {
        // Keep the original error message
      }
      
      return { success: false, error: errorMessage };
    }
  },

  // Logout
  async logout() {
    try {
      await api.auth.logout();
    } catch (error) {
      console.warn('Logout API call failed:', error);
    }
    
    this.clearSession();
    // Replace current page (dashboard/learning/etc) with login so back button
    // does not re-open protected pages from this session
    utils.navigateTo('index.html', true);
  },

  // Check Auth
  requireAuth() {
    const session = this.loadSession();
    if (session) {
      return true;
    }
    console.warn('No session found, redirecting to login.');
    // Use replace to avoid a broken page in the back-stack
    window.location.replace('index.html');
    return false;
  }
};