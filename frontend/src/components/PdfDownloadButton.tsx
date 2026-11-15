import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { fetchPdf, type PdfRequest } from '../lib/pdf'
import { Button } from './ui/button'

export const DetectiveReportContext = createContext(false)

export function PdfDownloadButton({ request, label }: { request: PdfRequest; label?: string }) {
  const detective = useContext(DetectiveReportContext)
  const controller = useRef<AbortController | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState(false)
  useEffect(() => {
    setLoading(false)
    setMessage('')
    return () => { controller.current?.abort(); controller.current = null }
  }, [request.url, request.body, detective])

  async function download() {
    if (controller.current) return
    const pending = new AbortController()
    controller.current = pending
    setLoading(true)
    setMessage('Подготовка PDF…')
    setError(false)
    try {
      const result = await fetchPdf(request, pending.signal, detective)
      if (pending.signal.aborted || controller.current !== pending) return
      const url = URL.createObjectURL(result.blob)
      const anchor = document.createElement('a')
      try {
        anchor.href = url
        anchor.download = result.filename
        anchor.rel = 'noopener'
        document.body.appendChild(anchor)
        anchor.click()
      } finally {
        anchor.remove()
        window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      }
      setMessage('PDF передан браузеру для скачивания.')
    } catch (reason) {
      if (pending.signal.aborted || controller.current !== pending) return
      setError(true)
      setMessage(reason instanceof Error ? reason.message : 'Не удалось скачать PDF. Повторите попытку.')
    } finally {
      if (controller.current === pending) { controller.current = null; setLoading(false) }
    }
  }
  return <div className="min-w-0 max-w-full space-y-1">
    <div className="flex flex-wrap gap-2">
      <Button type="button" variant="outline" size="sm" disabled={loading} onClick={() => void download()}>
        {loading ? 'Подготовка PDF…' : label ?? (detective ? 'Сформировать PDF-отчёт · Детектив' : 'Сформировать PDF-отчёт')}
      </Button>
      {loading && <Button type="button" variant="outline" size="sm" onClick={() => {
        controller.current?.abort(); controller.current = null
        setLoading(false); setError(false); setMessage('Загрузка PDF отменена.')
      }}>Отменить PDF</Button>}
    </div>
    <p role={error ? 'alert' : 'status'} className="max-w-sm break-words text-xs text-muted">{message}</p>
  </div>
}
