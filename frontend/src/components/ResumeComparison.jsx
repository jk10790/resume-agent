import React, { useState, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './ResumeComparison.css'

// Simple but effective diff algorithm using line-by-line comparison
export function diffLines(original, tailored) {
  // Handle null/undefined/empty inputs
  if (!original) original = ''
  if (!tailored) tailored = ''
  
  const origLines = original.split('\n')
  const tailLines = tailored.split('\n')
  const result = []
  
  // Use a simple longest common subsequence approach
  const maxLen = Math.max(origLines.length, tailLines.length)
  
  for (let i = 0; i < maxLen; i++) {
    const origLine = origLines[i] || ''
    const tailLine = tailLines[i] || ''
    
    if (origLine === tailLine) {
      result.push({ type: 'equal', original: origLine, tailored: tailLine })
    } else if (!origLine) {
      // Added line
      result.push({ type: 'added', original: '', tailored: tailLine })
    } else if (!tailLine) {
      // Removed line
      result.push({ type: 'removed', original: origLine, tailored: '' })
    } else {
      // Modified line - do word-level diff
      const wordDiff = diffWords(origLine, tailLine)
      result.push({ type: 'modified', original: origLine, tailored: tailLine, wordDiff })
    }
  }
  
  return result
}

export function diffWords(original, tailored) {
  // Handle null/undefined/empty inputs
  if (!original) original = ''
  if (!tailored) tailored = ''
  
  const origWords = original.split(/(\s+)/)
  const tailWords = tailored.split(/(\s+)/)
  const result = []
  
  let origIdx = 0
  let tailIdx = 0
  
  while (origIdx < origWords.length || tailIdx < tailWords.length) {
    if (origIdx >= origWords.length) {
      result.push({ type: 'added', text: tailWords[tailIdx] })
      tailIdx++
    } else if (tailIdx >= tailWords.length) {
      result.push({ type: 'removed', text: origWords[origIdx] })
      origIdx++
    } else if (origWords[origIdx] === tailWords[tailIdx]) {
      result.push({ type: 'equal', text: origWords[origIdx] })
      origIdx++
      tailIdx++
    } else {
      // Check if we can find a match ahead
      let foundMatch = false
      for (let lookAhead = 1; lookAhead <= 3 && tailIdx + lookAhead < tailWords.length; lookAhead++) {
        if (origWords[origIdx] === tailWords[tailIdx + lookAhead]) {
          // Add the words in between as added
          for (let j = 0; j < lookAhead; j++) {
            result.push({ type: 'added', text: tailWords[tailIdx + j] })
          }
          tailIdx += lookAhead
          foundMatch = true
          break
        }
      }
      
      if (!foundMatch) {
        // Check if we can find a match ahead in original
        for (let lookAhead = 1; lookAhead <= 3 && origIdx + lookAhead < origWords.length; lookAhead++) {
          if (origWords[origIdx + lookAhead] === tailWords[tailIdx]) {
            // Mark the words in between as removed
            for (let j = 0; j < lookAhead; j++) {
              result.push({ type: 'removed', text: origWords[origIdx + j] })
            }
            origIdx += lookAhead
            foundMatch = true
            break
          }
        }
      }
      
      if (!foundMatch) {
        result.push({ type: 'removed', text: origWords[origIdx] })
        result.push({ type: 'added', text: tailWords[tailIdx] })
        origIdx++
        tailIdx++
      }
    }
  }
  
  return result
}

function ResumeComparison({ original, tailored, onClose }) {
  const [viewMode, setViewMode] = useState('diff') // Default to diff view to show changes
  const [syncScroll, setSyncScroll] = useState(true)

  const handleScroll = (e, targetId) => {
    if (!syncScroll) return
    
    const source = e.target
    const target = document.getElementById(targetId)
    
    if (target && source) {
      const scrollPercentage = source.scrollTop / (source.scrollHeight - source.clientHeight)
      target.scrollTop = scrollPercentage * (target.scrollHeight - target.clientHeight)
    }
  }

  const diffResult = useMemo(() => diffLines(original, tailored), [original, tailored])

  const renderDiffView = () => {
    return (
      <div className="diff-view-enhanced">
        <div className="panel-header">
          Changes Highlighted
          <span className="diff-legend">
            <span className="legend-item">
              <span className="legend-color diff-added"></span> Added
            </span>
            <span className="legend-item">
              <span className="legend-color diff-removed"></span> Removed
            </span>
            <span className="legend-item">
              <span className="legend-color diff-modified"></span> Modified
            </span>
          </span>
        </div>
        <div className="resume-content scrollable">
          <div className="diff-content-enhanced">
            {diffResult.map((line, idx) => {
              if (line.type === 'equal') {
                return (
                  <div key={idx} className="diff-line diff-equal">
                    {line.original}
                  </div>
                )
              } else if (line.type === 'added') {
                return (
                  <div key={idx} className="diff-line diff-added-line">
                    <span className="diff-marker">+</span>
                    <span className="diff-text">{line.tailored}</span>
                  </div>
                )
              } else if (line.type === 'removed') {
                return (
                  <div key={idx} className="diff-line diff-removed-line">
                    <span className="diff-marker">-</span>
                    <span className="diff-text">{line.original}</span>
                  </div>
                )
              } else if (line.type === 'modified') {
                return (
                  <div key={idx} className="diff-line diff-modified-line">
                    <div className="diff-line-removed">
                      <span className="diff-marker">-</span>
                      <span className="diff-text">{line.original}</span>
                    </div>
                    <div className="diff-line-added">
                      <span className="diff-marker">+</span>
                      <span className="diff-text">
                        {line.wordDiff.map((word, wordIdx) => {
                          if (word.type === 'equal') {
                            return <span key={wordIdx}>{word.text}</span>
                          } else if (word.type === 'added') {
                            return <span key={wordIdx} className="diff-word-added">{word.text}</span>
                          } else if (word.type === 'removed') {
                            return <span key={wordIdx} className="diff-word-removed">{word.text}</span>
                          }
                          return null
                        })}
                      </span>
                    </div>
                  </div>
                )
              }
              return null
            })}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="resume-comparison-overlay">
      <div className="resume-comparison-container">
        <div className="comparison-header">
          <h2>📄 Resume Comparison</h2>
          <div className="comparison-controls">
            <div className="view-mode-selector">
              <button
                className={viewMode === 'side-by-side' ? 'active' : ''}
                onClick={() => setViewMode('side-by-side')}
              >
                Side-by-Side
              </button>
              <button
                className={viewMode === 'original' ? 'active' : ''}
                onClick={() => setViewMode('original')}
              >
                Original Only
              </button>
              <button
                className={viewMode === 'tailored' ? 'active' : ''}
                onClick={() => setViewMode('tailored')}
              >
                Tailored Only
              </button>
              <button
                className={viewMode === 'diff' ? 'active' : ''}
                onClick={() => setViewMode('diff')}
              >
                Diff View
              </button>
            </div>
            {viewMode === 'side-by-side' && (
              <label className="sync-scroll-toggle">
                <input
                  type="checkbox"
                  checked={syncScroll}
                  onChange={(e) => setSyncScroll(e.target.checked)}
                />
                Sync Scroll
              </label>
            )}
            <button className="close-button" onClick={onClose}>
              ✕ Close
            </button>
          </div>
        </div>

        <div className="comparison-content">
          {viewMode === 'side-by-side' && (
            <div className="side-by-side-view">
              <div className="resume-panel">
                <div className="panel-header">Original Resume</div>
                <div
                  className="resume-content scrollable"
                  onScroll={(e) => handleScroll(e, 'tailored-content')}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]} className="resume-markdown">
                    {original}
                  </ReactMarkdown>
                </div>
              </div>
              <div className="resume-panel">
                <div className="panel-header">Tailored Resume</div>
                <div
                  id="tailored-content"
                  className="resume-content scrollable"
                  onScroll={(e) => handleScroll(e, 'original-content')}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]} className="resume-markdown">
                    {tailored}
                  </ReactMarkdown>
                </div>
              </div>
            </div>
          )}

          {viewMode === 'original' && (
            <div className="single-view">
              <div className="panel-header">Original Resume</div>
              <div className="resume-content scrollable">
                <ReactMarkdown remarkPlugins={[remarkGfm]} className="resume-markdown">
                  {original}
                </ReactMarkdown>
              </div>
            </div>
          )}

          {viewMode === 'tailored' && (
            <div className="single-view">
              <div className="panel-header">Tailored Resume</div>
              <div className="resume-content scrollable">
                <ReactMarkdown remarkPlugins={[remarkGfm]} className="resume-markdown">
                  {tailored}
                </ReactMarkdown>
              </div>
            </div>
          )}

          {viewMode === 'diff' && renderDiffView()}
        </div>
      </div>
    </div>
  )
}

export default ResumeComparison
