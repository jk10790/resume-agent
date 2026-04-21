import React, { useState, useEffect } from 'react'
import DiscoverRoles from './components/DiscoverRoles'
import TailorResume from './components/TailorResume'
import StrategyBriefs from './components/StrategyBriefs'
import ExtractJD from './components/ExtractJD'
import Applications from './components/Applications'
import GoogleLogin from './components/GoogleLogin'
import './App.css'

function App() {
  const [activeTab, setActiveTab] = useState('tailor')
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [discoverAvailable, setDiscoverAvailable] = useState(false)
  const [tailorDiscoverSeed, setTailorDiscoverSeed] = useState(null)
  const [tailorLoadBriefId, setTailorLoadBriefId] = useState(null)
  const [applicationsSearchSeed, setApplicationsSearchSeed] = useState('')
  const [strategiesSearchSeed, setStrategiesSearchSeed] = useState('')

  const handleAuthChange = (authenticated) => {
    setIsAuthenticated(authenticated)
  }

  // Check auth status on mount
  useEffect(() => {
    const checkAuth = async () => {
      try {
        const [authResponse, discoverResponse] = await Promise.all([
          fetch('/api/auth/google/status', {
            credentials: 'include'
          }),
          fetch('/api/discover/status', {
            credentials: 'include'
          }).catch(() => null)
        ])
        const data = await authResponse.json()
        setIsAuthenticated(data.authenticated)

        if (discoverResponse && discoverResponse.ok) {
          const discoverData = await discoverResponse.json()
          const available = Boolean(discoverData.enabled && discoverData.configured)
          setDiscoverAvailable(available)
          if (!available) {
            setActiveTab((currentTab) => (currentTab === 'discover' ? 'tailor' : currentTab))
          }
        } else {
          setDiscoverAvailable(false)
        }
      } catch (error) {
        console.error('Error checking auth status:', error)
        setDiscoverAvailable(false)
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
        {discoverAvailable && (
          <button
            className={activeTab === 'discover' ? 'active' : ''}
            onClick={() => setActiveTab('discover')}
          >
            🔎 Discover
          </button>
        )}
        <button
          className={activeTab === 'tailor' ? 'active' : ''}
          onClick={() => setActiveTab('tailor')}
        >
          ✂️ Tailor Resume
        </button>
        <button
          className={activeTab === 'strategies' ? 'active' : ''}
          onClick={() => setActiveTab('strategies')}
        >
          🧭 Strategies
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
        {discoverAvailable && activeTab === 'discover' && (
          <DiscoverRoles
            isAuthenticated={isAuthenticated}
            onOpenInTailor={(seed) => {
              setTailorDiscoverSeed(seed)
              setActiveTab('tailor')
            }}
          />
        )}
        {activeTab === 'tailor' && (
          <TailorResume
            loadBriefId={tailorLoadBriefId}
            onConsumedLoadBrief={() => setTailorLoadBriefId(null)}
            discoverSeed={tailorDiscoverSeed}
            onConsumedDiscoverSeed={() => setTailorDiscoverSeed(null)}
            onBrowseStrategies={(query) => {
              setStrategiesSearchSeed(query || '')
              setActiveTab('strategies')
            }}
            onViewApplications={(query) => {
              setApplicationsSearchSeed(query || '')
              setActiveTab('applications')
            }}
          />
        )}
        {activeTab === 'strategies' && (
          <StrategyBriefs
            initialSearchQuery={strategiesSearchSeed}
            onConsumedInitialSearch={() => setStrategiesSearchSeed('')}
            onOpenInTailor={(briefId) => {
              setTailorLoadBriefId(briefId)
              setActiveTab('tailor')
            }}
            onViewApplications={(query) => {
              setApplicationsSearchSeed(query || '')
              setActiveTab('applications')
            }}
          />
        )}
        {activeTab === 'extract' && <ExtractJD />}
        {activeTab === 'applications' && (
          <Applications
            initialSearchQuery={applicationsSearchSeed}
            onConsumedInitialSearch={() => setApplicationsSearchSeed('')}
            onOpenStrategyInTailor={(briefId) => {
              setTailorLoadBriefId(briefId)
              setActiveTab('tailor')
            }}
            onBrowseStrategies={(query) => {
              setStrategiesSearchSeed(query || '')
              setActiveTab('strategies')
            }}
          />
        )}
      </main>
    </div>
  )
}

export default App
