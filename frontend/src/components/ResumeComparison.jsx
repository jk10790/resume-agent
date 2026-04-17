import React, { useState, useMemo, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './ResumeComparison.css'

function normalizeLine(line) {
  return (line || '').replace(/\s+/g, ' ').trim()
}

function splitIntoLines(text) {
  return (text || '')
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map(line => line.replace(/\s+$/g, ''))
}

function detectSectionLabel(line) {
  const trimmed = (line || '').trim()
  if (!trimmed) return null
  const markdownHeading = trimmed.match(/^#{1,6}\s+(.+)$/)
  if (markdownHeading) return markdownHeading[1].trim()
  const boldHeading = trimmed.match(/^\*\*(.+)\*\*$/)
  if (boldHeading) return boldHeading[1].trim()
  if (/^[A-Z][A-Z ]+$/.test(trimmed) && trimmed.length < 40) return trimmed
  return null
}

// LCS-backed line diff so moved or inserted lines do not scramble the whole comparison.
export function diffLines(original, tailored) {
  const origLines = splitIntoLines(original)
  const tailLines = splitIntoLines(tailored)
  const n = origLines.length
  const m = tailLines.length
  const dp = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0))

  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      if (normalizeLine(origLines[i]) === normalizeLine(tailLines[j])) {
        dp[i][j] = dp[i + 1][j + 1] + 1
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1])
      }
    }
  }

  const result = []
  let i = 0
  let j = 0

  while (i < n && j < m) {
    if (normalizeLine(origLines[i]) === normalizeLine(tailLines[j])) {
      result.push({ type: 'equal', original: origLines[i], tailored: tailLines[j] })
      i++
      j++
      continue
    }

    if (dp[i + 1][j] === dp[i][j + 1]) {
      result.push({
        type: 'modified',
        original: origLines[i],
        tailored: tailLines[j],
        wordDiff: diffWords(origLines[i], tailLines[j]),
      })
      i++
      j++
      continue
    }

    if (dp[i + 1][j] > dp[i][j + 1]) {
      result.push({ type: 'removed', original: origLines[i], tailored: '' })
      i++
    } else {
      result.push({ type: 'added', original: '', tailored: tailLines[j] })
      j++
    }
  }

  while (i < n) {
    result.push({ type: 'removed', original: origLines[i], tailored: '' })
    i++
  }

  while (j < m) {
    result.push({ type: 'added', original: '', tailored: tailLines[j] })
    j++
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

function buildDisplayDiff(lines) {
  const display = []
  let equalBuffer = []

  const flushEqualBuffer = () => {
    if (equalBuffer.length === 0) return
    if (equalBuffer.length <= 4) {
      display.push(...equalBuffer)
    } else {
      display.push(equalBuffer[0], equalBuffer[1])
      display.push({ type: 'collapsed', count: equalBuffer.length - 4 })
      display.push(equalBuffer[equalBuffer.length - 2], equalBuffer[equalBuffer.length - 1])
    }
    equalBuffer = []
  }

  for (const line of lines) {
    if (line.type === 'equal') {
      equalBuffer.push(line)
    } else {
      flushEqualBuffer()
      display.push(line)
    }
  }

  flushEqualBuffer()
  return display
}

function summarizeDiff(lines) {
  return {
    added: lines.filter(line => line.type === 'added').length,
    removed: lines.filter(line => line.type === 'removed').length,
    modified: lines.filter(line => line.type === 'modified').length,
  }
}

function summarizeSemanticChanges(lines) {
  const sectionStats = new Map()
  let currentSection = 'Header'

  for (const line of lines) {
    if (line.type === 'section-header') {
      currentSection = line.section || 'Resume'
      if (!sectionStats.has(currentSection)) {
        sectionStats.set(currentSection, { added: 0, removed: 0, modified: 0 })
      }
      continue
    }
    if (line.type === 'collapsed' || line.type === 'equal') continue
    if (!sectionStats.has(currentSection)) {
      sectionStats.set(currentSection, { added: 0, removed: 0, modified: 0 })
    }
    const stats = sectionStats.get(currentSection)
    if (line.type === 'added') stats.added += 1
    if (line.type === 'removed') stats.removed += 1
    if (line.type === 'modified') stats.modified += 1
  }

  return Array.from(sectionStats.entries())
    .map(([section, stats]) => ({ section, ...stats, total: stats.added + stats.removed + stats.modified }))
    .filter((item) => item.total > 0)
    .sort((a, b) => b.total - a.total)
}

function buildRiskSignals(validation, reviewBundle) {
  const metricFlags = (validation?.metric_provenance?.flagged_details || []).map((item) => ({
    type: 'metric',
    raw: item.raw || '',
    line: item.line || '',
    label: `Unverified metric: ${item.raw || 'number'}`,
  }))
  const authenticityFlags = (reviewBundle?.authenticity?.issues || [])
    .filter((issue) => issue.evidence || issue.message)
    .map((issue) => ({
      type: issue.category || 'authenticity',
      raw: issue.evidence || issue.message || '',
      line: issue.evidence || '',
      label: issue.message || 'Potential unsupported claim',
    }))
  return [...metricFlags, ...authenticityFlags]
}

function inferHunkIntent(hunk, reviewBundle, riskSignals) {
  const originalText = normalizeLine(hunk.originalBlock)
  const tailoredText = normalizeLine(hunk.tailoredBlock)
  const combinedText = `${originalText} ${tailoredText}`.trim()
  const section = (hunk.section || '').toLowerCase()

  const risks = [
    ...matchRiskSignals(hunk.originalBlock, riskSignals),
    ...matchRiskSignals(hunk.tailoredBlock, riskSignals),
  ]
  if (risks.length > 0) {
    return {
      label: 'Authenticity risk',
      reason: risks[0].label,
      tone: 'risk',
    }
  }

  const atsIssues = reviewBundle?.ats_parse?.issues || []
  const atsSignals = ['email', 'phone', 'linkedin', 'github', 'summary', 'experience', 'education', 'skills']
  if (
    atsIssues.length > 0 &&
    (section === 'header' || atsSignals.some((token) => combinedText.includes(token)))
  ) {
    return {
      label: 'ATS cleanup',
      reason: 'This hunk appears to normalize structure or parser-visible formatting.',
      tone: 'ats',
    }
  }

  const missingKeywords = (reviewBundle?.job_match?.metrics?.missing_keywords || []).map((keyword) => String(keyword).toLowerCase())
  const matchedKeyword = missingKeywords.find((keyword) => keyword && tailoredText.includes(keyword))
  if (matchedKeyword) {
    return {
      label: 'Keyword alignment',
      reason: `Adds or restores JD language like "${matchedKeyword}".`,
      tone: 'keyword',
    }
  }

  const editorialIssues = reviewBundle?.editorial?.issues || []
  const readabilitySignals = ['led ', 'built ', 'developed ', 'implemented ', 'designed ', 'optimized ', 'improved ']
  if (
    editorialIssues.length > 0 ||
    readabilitySignals.some((token) => tailoredText.includes(token))
  ) {
    return {
      label: 'Editorial rewrite',
      reason: 'This change mainly tightens phrasing, readability, or emphasis.',
      tone: 'editorial',
    }
  }

  return {
    label: 'Content adjustment',
    reason: 'This hunk changes resume content without a stronger inferred system motive.',
    tone: 'neutral',
  }
}

function matchRiskSignals(text, riskSignals) {
  const line = normalizeLine(text)
  if (!line) return []
  return riskSignals.filter((signal) => {
    const raw = normalizeLine(signal.raw)
    const context = normalizeLine(signal.line)
    return (raw && line.includes(raw)) || (context && line.includes(context)) || (line && raw && raw.includes(line))
  })
}

function annotateDiffSections(lines) {
  const annotated = []
  let currentOriginalSection = 'Header'
  let currentTailoredSection = 'Header'
  let lastRenderedSection = null

  for (const line of lines) {
    const originalSection = detectSectionLabel(line.original) || currentOriginalSection
    const tailoredSection = detectSectionLabel(line.tailored) || currentTailoredSection

    currentOriginalSection = originalSection
    currentTailoredSection = tailoredSection

    const effectiveSection = tailoredSection || originalSection || 'Resume'
    if (effectiveSection !== lastRenderedSection) {
      annotated.push({ type: 'section-header', section: effectiveSection })
      lastRenderedSection = effectiveSection
    }

    annotated.push({
      ...line,
      section: effectiveSection,
    })
  }

  return annotated
}

function buildDiffHunks(lines) {
  const hunks = []
  let currentSection = 'Header'
  let currentHunk = null
  let lastEqualLine = ''

  const flush = () => {
    if (!currentHunk || currentHunk.lines.length === 0) return
    const originalBlock = currentHunk.lines
      .filter((line) => line.type !== 'added')
      .map((line) => line.original)
      .join('\n')
      .trim()
    const tailoredBlock = currentHunk.lines
      .filter((line) => line.type !== 'removed')
      .map((line) => line.tailored)
      .join('\n')
      .trim()
    hunks.push({
      id: `${currentSection}-${hunks.length}`,
      section: currentSection,
      lines: currentHunk.lines,
      originalBlock,
      tailoredBlock,
      beforeAnchor: currentHunk.beforeAnchor || '',
      afterAnchor: currentHunk.afterAnchor || '',
    })
    currentHunk = null
  }

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]
    if (line.type === 'equal') {
      const sectionLabel = detectSectionLabel(line.original) || detectSectionLabel(line.tailored)
      if (sectionLabel) {
        currentSection = sectionLabel
      }
      lastEqualLine = line.tailored || line.original || lastEqualLine
      if (currentHunk && !currentHunk.afterAnchor) {
        currentHunk.afterAnchor = line.tailored || line.original || ''
      }
      flush()
      continue
    }
    const originalSection = detectSectionLabel(line.original)
    const tailoredSection = detectSectionLabel(line.tailored)
    if (tailoredSection || originalSection) {
      flush()
      currentSection = tailoredSection || originalSection || currentSection
    }
    if (!currentHunk) {
      currentHunk = { lines: [], beforeAnchor: lastEqualLine, afterAnchor: '' }
    }
    currentHunk.lines.push(line)
  }
  flush()
  return hunks
}

function insertBlockUsingAnchors(currentText, blockText, beforeAnchor, afterAnchor) {
  if (!blockText) return currentText
  const current = currentText || ''
  const block = blockText.trim()
  if (!block || current.includes(block)) {
    return current
  }
  if (beforeAnchor && current.includes(beforeAnchor)) {
    return current.replace(beforeAnchor, `${beforeAnchor}\n${block}`)
  }
  if (afterAnchor && current.includes(afterAnchor)) {
    return current.replace(afterAnchor, `${block}\n${afterAnchor}`)
  }
  return `${current.trim()}\n${block}`.trim()
}

function applyHunkWithAnchors(currentText, fromBlock, toBlock, beforeAnchor, afterAnchor) {
  const current = currentText || ''
  const from = (fromBlock || '').trim()
  const to = (toBlock || '').trim()
  if (from && current.includes(from)) {
    return current.replace(from, to)
  }
  if (!from && to) {
    return insertBlockUsingAnchors(current, to, beforeAnchor, afterAnchor)
  }
  if (from && !to && current.includes(from)) {
    return current.replace(from, '').replace(/\n{3,}/g, '\n\n').trim()
  }
  return current
}

function renderWordDiff(words, mode) {
  return words.map((word, idx) => {
    if (word.type === 'equal') {
      return <span key={idx}>{word.text}</span>
    }
    if (mode === 'original' && word.type === 'removed') {
      return <span key={idx} className="diff-word-removed">{word.text}</span>
    }
    if (mode === 'tailored' && word.type === 'added') {
      return <span key={idx} className="diff-word-added">{word.text}</span>
    }
    return null
  })
}

function ResumeComparison({ original, tailored, baseTailored, validation, reviewBundle, onClose, onApplyHunk, isApplyingHunk }) {
  const [viewMode, setViewMode] = useState('diff') // Default to diff view to show changes
  const [syncScroll, setSyncScroll] = useState(true)
  const comparisonSectionRefs = useRef({})

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
  const baseDiffResult = useMemo(() => diffLines(original, baseTailored || tailored), [original, baseTailored, tailored])
  const displayDiff = useMemo(() => buildDisplayDiff(diffResult), [diffResult])
  const diffSummary = useMemo(() => summarizeDiff(diffResult), [diffResult])
  const sectionedDisplayDiff = useMemo(() => annotateDiffSections(displayDiff), [displayDiff])
  const editableHunks = useMemo(() => buildDiffHunks(baseDiffResult), [baseDiffResult])
  const semanticSummary = useMemo(() => summarizeSemanticChanges(sectionedDisplayDiff), [sectionedDisplayDiff])
  const riskSignals = useMemo(() => buildRiskSignals(validation, reviewBundle), [validation, reviewBundle])
  const hunksWithIntent = useMemo(
    () => editableHunks.map((hunk) => ({ ...hunk, intent: inferHunkIntent(hunk, reviewBundle, riskSignals) })),
    [editableHunks, reviewBundle, riskSignals]
  )
  const sectionJumpList = useMemo(
    () => sectionedDisplayDiff.filter(line => line.type === 'section-header').map(line => line.section),
    [sectionedDisplayDiff]
  )

  const jumpToComparisonSection = (section) => {
    const node = comparisonSectionRefs.current[section]
    if (node && typeof node.scrollIntoView === 'function') {
      node.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }

  const getHunkStatus = (hunk) => {
    const current = tailored || ''
    if (hunk.tailoredBlock && current.includes(hunk.tailoredBlock)) {
      return 'accepted'
    }
    if (hunk.originalBlock && current.includes(hunk.originalBlock)) {
      return 'rejected'
    }
    return 'mixed'
  }

  const applyHunk = async (hunk, mode) => {
    if (!onApplyHunk) return
    const current = tailored || ''
    let updated = current
    if (mode === 'reject') {
      updated = applyHunkWithAnchors(current, hunk.tailoredBlock, hunk.originalBlock, hunk.beforeAnchor, hunk.afterAnchor)
    } else if (mode === 'accept') {
      updated = applyHunkWithAnchors(current, hunk.originalBlock, hunk.tailoredBlock, hunk.beforeAnchor, hunk.afterAnchor)
    }
    if (updated !== current) {
      await onApplyHunk(updated)
    }
  }

  const renderSideBySideDiffView = () => {
    return (
      <div className="git-side-by-side-view">
        <div className="panel-header">
          Git-Style Side-by-Side Comparison
          <span className="diff-legend">
            <span className="legend-item">
              <span className="legend-color diff-added"></span> Added
            </span>
            <span className="legend-item">
              <span className="legend-color diff-removed"></span> Removed
            </span>
            <span className="legend-item">
              <span className="legend-color diff-modified"></span> Rewritten
            </span>
          </span>
        </div>
        <div
          id="side-by-side-content"
          className="resume-content scrollable git-side-by-side-scroll"
        >
          {sectionJumpList.length > 0 && (
            <div className="comparison-section-jumps">
              {sectionJumpList.map((section) => (
                <button
                  key={section}
                  type="button"
                  className="comparison-section-jump"
                  onClick={() => jumpToComparisonSection(section)}
                >
                  {section}
                </button>
              ))}
            </div>
          )}
          <div className="comparison-summary">
            <span><strong>{diffSummary.added}</strong> additions</span>
            <span><strong>{diffSummary.removed}</strong> removals</span>
            <span><strong>{diffSummary.modified}</strong> rewrites</span>
          </div>
          {semanticSummary.length > 0 && (
            <div className="semantic-summary">
              {semanticSummary.slice(0, 6).map((item) => (
                <div key={item.section} className="semantic-summary-item">
                  <strong>{item.section}:</strong>{' '}
                  {item.modified > 0 && <span>{item.modified} rewritten{item.modified > 1 ? ' lines' : ' line'}</span>}
                  {item.modified > 0 && item.added > 0 && <span>, </span>}
                  {item.added > 0 && <span>{item.added} added</span>}
                  {(item.modified > 0 || item.added > 0) && item.removed > 0 && <span>, </span>}
                  {item.removed > 0 && <span>{item.removed} removed</span>}
                </div>
              ))}
            </div>
          )}
          {hunksWithIntent.length > 0 && (
            <div className="diff-hunk-toolbar">
              {hunksWithIntent.slice(0, 8).map((hunk, index) => {
                const status = getHunkStatus(hunk)
                return (
                  <div key={hunk.id} className={`diff-hunk-card status-${status}`}>
                    <div className="diff-hunk-label">
                      Hunk {index + 1} · {hunk.section}
                    </div>
                    <div className={`diff-hunk-intent tone-${hunk.intent?.tone || 'neutral'}`}>
                      {hunk.intent?.label || 'Content adjustment'}
                    </div>
                    {hunk.intent?.reason && (
                      <div className="diff-hunk-reason">
                        {hunk.intent.reason}
                      </div>
                    )}
                    <div className="diff-hunk-actions">
                      <button
                        type="button"
                        className="comparison-hunk-button"
                        onClick={() => applyHunk(hunk, 'accept')}
                        disabled={isApplyingHunk || status === 'accepted'}
                      >
                        Accept
                      </button>
                      <button
                        type="button"
                        className="comparison-hunk-button"
                        onClick={() => applyHunk(hunk, 'reject')}
                        disabled={isApplyingHunk || status === 'rejected'}
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
          <div className="git-diff-grid">
            <div className="git-diff-column-header">Original</div>
            <div className="git-diff-column-header">Tailored</div>
            {sectionedDisplayDiff.map((line, idx) => {
              if (line.type === 'section-header') {
                return (
                  <div
                    key={idx}
                    className="git-diff-section-row"
                    ref={(node) => {
                      if (node) comparisonSectionRefs.current[line.section] = node
                    }}
                  >
                    <span className="git-diff-section-badge">{line.section}</span>
                  </div>
                )
              }
              if (line.type === 'collapsed') {
                return (
                  <div key={idx} className="git-diff-collapsed-row">
                    {line.count} unchanged lines hidden
                  </div>
                )
              }

              const rowClass = `git-diff-row git-diff-row-${line.type}`
              const originalCellClass = `git-diff-cell git-diff-cell-original ${line.type === 'added' ? 'empty' : ''}`
              const tailoredCellClass = `git-diff-cell git-diff-cell-tailored ${line.type === 'removed' ? 'empty' : ''}`
              const originalRisks = matchRiskSignals(line.original, riskSignals)
              const tailoredRisks = matchRiskSignals(line.tailored, riskSignals)

              return (
                <React.Fragment key={idx}>
                  <div className={`${rowClass} ${originalCellClass}`}>
                    <span className="git-diff-sign">
                      {line.type === 'added' ? '' : line.type === 'removed' ? '-' : line.type === 'modified' ? '~' : ' '}
                    </span>
                    <span className="git-diff-line-text">
                      {line.type === 'modified' && line.wordDiff
                        ? renderWordDiff(line.wordDiff, 'original')
                        : line.original}
                    </span>
                    {originalRisks.length > 0 && (
                      <span className="git-diff-risk-badge" title={originalRisks.map((risk) => risk.label).join('\n')}>
                        Review
                      </span>
                    )}
                  </div>
                  <div className={`${rowClass} ${tailoredCellClass}`}>
                    <span className="git-diff-sign">
                      {line.type === 'removed' ? '' : line.type === 'added' ? '+' : line.type === 'modified' ? '~' : ' '}
                    </span>
                    <span className="git-diff-line-text">
                      {line.type === 'modified' && line.wordDiff
                        ? renderWordDiff(line.wordDiff, 'tailored')
                        : line.tailored}
                    </span>
                    {tailoredRisks.length > 0 && (
                      <span className="git-diff-risk-badge" title={tailoredRisks.map((risk) => risk.label).join('\n')}>
                        Review
                      </span>
                    )}
                  </div>
                </React.Fragment>
              )
            })}
          </div>
        </div>
      </div>
    )
  }

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
          <div className="comparison-summary">
            <span><strong>{diffSummary.added}</strong> additions</span>
            <span><strong>{diffSummary.removed}</strong> removals</span>
            <span><strong>{diffSummary.modified}</strong> rewrites</span>
          </div>
          {semanticSummary.length > 0 && (
            <div className="semantic-summary">
              {semanticSummary.slice(0, 6).map((item) => (
                <div key={item.section} className="semantic-summary-item">
                  <strong>{item.section}:</strong> {item.total} changed line{item.total > 1 ? 's' : ''}
                </div>
              ))}
            </div>
          )}
          <div className="diff-content-enhanced">
            {displayDiff.map((line, idx) => {
              if (line.type === 'collapsed') {
                return (
                  <div key={idx} className="diff-line diff-collapsed">
                    {line.count} unchanged lines hidden
                  </div>
                )
              } else if (line.type === 'equal') {
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
            renderSideBySideDiffView()
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
