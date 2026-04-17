import React, { useState, useEffect } from 'react';
import './GoogleLogin.css';

const GoogleLogin = ({ onAuthChange }) => {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [oauthConfigured, setOauthConfigured] = useState(true);
  const [backendAvailable, setBackendAvailable] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    checkAuthStatus();
  }, []);

  const checkAuthStatus = async () => {
    try {
      setError(null);
      const response = await fetch('/api/auth/google/status', {
        credentials: 'include'
      });
      if (!response.ok) {
        throw new Error(`Auth status request failed with ${response.status}`);
      }
      const data = await response.json();
      
      setIsAuthenticated(data.authenticated);
      setUser(data.user || null);
      setOauthConfigured(Boolean(data.oauth_configured));
      setBackendAvailable(true);
      
      if (onAuthChange) {
        onAuthChange(data.authenticated, data.user);
      }
    } catch (error) {
      console.error('Error checking auth status:', error);
      setIsAuthenticated(false);
      setBackendAvailable(false);
      setError('Cannot reach the backend at http://localhost:8000. Check /tmp/resume-agent-backend.log.');
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = () => {
    // Redirect to backend OAuth endpoint
    window.location.href = '/api/auth/google/login';
  };

  const handleLogout = async () => {
    try {
      const response = await fetch('/api/auth/google/logout', {
        credentials: 'include'
      });
      const data = await response.json();
      
      if (data.success) {
        setIsAuthenticated(false);
        setUser(null);
        
        if (onAuthChange) {
          onAuthChange(false, null);
        }
      }
    } catch (error) {
      console.error('Error logging out:', error);
    }
  };

  // Check for auth success/error in URL params
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const authStatus = urlParams.get('auth');
    const error = urlParams.get('error');
    
    if (authStatus === 'success') {
      // Clean URL and refresh auth status
      window.history.replaceState({}, document.title, window.location.pathname);
      checkAuthStatus();
    } else if (error) {
      console.error('OAuth error:', error);
      // Clean URL
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  }, []);

  if (loading) {
    return (
      <div className="google-login">
        <div className="auth-loading">Checking authentication...</div>
      </div>
    );
  }

  if (isAuthenticated && user) {
    return (
      <div className="google-login">
        <div className="auth-status authenticated">
          <div className="user-info">
            {user.picture && (
              <img src={user.picture} alt={user.name || 'User'} className="user-avatar" />
            )}
            <div className="user-details">
              <div className="user-name">{user.name || 'User'}</div>
              <div className="user-email">{user.email}</div>
            </div>
          </div>
          <button onClick={handleLogout} className="logout-btn">
            Logout
          </button>
        </div>
      </div>
    );
  }

  if (!backendAvailable && !isAuthenticated) {
    return (
      <div className="google-login">
        <div className="auth-status not-authenticated">
          <div className="auth-prompt">
            <p className="auth-warning">
              Backend is unavailable. The frontend cannot reach `/api/auth/google/status`.
            </p>
            {error && <p className="auth-error">{error}</p>}
          </div>
        </div>
      </div>
    );
  }

  // Show error if OAuth not configured
  if (!oauthConfigured && !isAuthenticated) {
    return (
      <div className="google-login">
        <div className="auth-status not-authenticated">
          <div className="auth-prompt">
            <p className="auth-warning">
              ⚠️ Google OAuth is not configured. Please set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in your .env file.
            </p>
            {error && <p className="auth-error">{error}</p>}
            <p className="auth-info">
              See ENV_CONFIG_REFERENCE.md for setup instructions.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="google-login">
      <div className="auth-status not-authenticated">
        <div className="auth-prompt">
          <p>Sign in with Google to access your Drive files</p>
          <button onClick={handleLogin} className="google-signin-btn" disabled={!oauthConfigured}>
            <svg className="google-icon" viewBox="0 0 24 24">
              <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
              <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
              <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
              <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
            </svg>
            Sign in with Google
          </button>
        </div>
      </div>
    </div>
  );
};

export default GoogleLogin;
