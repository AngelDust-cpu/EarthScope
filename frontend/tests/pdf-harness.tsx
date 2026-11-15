// Isolated browser fixture: simulated reports, never presented as NASA observations.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { DetectiveReportContext, PdfDownloadButton } from '../src/components/PdfDownloadButton'
import { analysisPdfRequest, safePdfFilename } from '../src/lib/pdf'
import '../src/index.css'

Object.assign(window, { analysisPdfRequest, safePdfFilename })
const query = new URLSearchParams(location.search)
const request = { url: '/api/trend?latitude=42&longitude=74&variable=temperature&start_year=1990&end_year=2025',
  ...(query.has('post') ? { body: JSON.stringify({ point_a: { latitude: 42, longitude: 74 } }) } : {}) }
createRoot(document.getElementById('root')!).render(
  <DetectiveReportContext.Provider value={query.has('detective')}>
    <div style={{ maxWidth: '100%', padding: 12 }}><PdfDownloadButton request={request} /></div>
  </DetectiveReportContext.Provider>,
)
