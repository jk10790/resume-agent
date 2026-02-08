import React, { useState, useEffect } from 'react'
import TailorResume from './components/TailorResume'
import ExtractJD from './components/ExtractJD'
import Applications from './components/Applications'
import GoogleLogin from './components/GoogleLogin'
import './App.css'

function App() {
  const [activeTab, setActiveTab] = useState('tailor')
  const [isAuthenticated, setIsAuthenticated] = useState(false)

  const handleAuthChange = (authenticated) => {
    setIsAuthenticated(authenticated)
  }

  // Check auth status on mount
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const response = await fetch('/api/auth/google/status', {
          credentials: 'include'
        })
        const data = await response.json()
        setIsAuthenticated(data.authenticated)
      } catch (error) {
        console.error('Error checking auth status:', error)
      }
    }
    checkAuth()
  }, [])

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-content">
          <div className="header-title">
            <h1>📄 Resume Agent</h1>
            <p>AI-powered resume tailoring and job application assistant</p>
          </div>
          <div className="header-auth">
            <GoogleLogin onAuthChange={handleAuthChange} />
          </div>
        </div>
      </header>

      <nav className="tabs">
        <button
          className={activeTab === 'tailor' ? 'active' : ''}
          onClick={() => setActiveTab('tailor')}
        >
          ✂️ Tailor Resume
        </button>
        <button
          className={activeTab === 'extract' ? 'active' : ''}
          onClick={() => setActiveTab('extract')}
        >
          📄 Extract Job Description
        </button>
        <button
          className={activeTab === 'applications' ? 'active' : ''}
          onClick={() => setActiveTab('applications')}
        >
          📊 Applications
        </button>
      </nav>

      <main className="main-content">
        {activeTab === 'tailor' && <TailorResume />}
        {activeTab === 'extract' && <ExtractJD />}
        {activeTab === 'applications' && <Applications />}
      </main>
    </div>
  )
}

export default App
