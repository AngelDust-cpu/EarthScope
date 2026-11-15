import { useCallback, useEffect, useRef, useState } from 'react'

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: Error | null
}

/**
 * Runs `task` whenever `key` changes. Superseded requests are aborted, stale
 * results are ignored, and the initial state is an explicit loading state so the
 * UI can show a real skeleton rather than an empty or fake chart.
 */
export function useAsyncData<T>(
  key: string | null,
  task: (signal: AbortSignal) => Promise<T>,
): AsyncState<T> & { retry: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ data: null, loading: true, error: null })
  const [attempt, setAttempt] = useState(0)
  const taskRef = useRef(task)
  taskRef.current = task

  useEffect(() => {
    if (key === null) return
    const controller = new AbortController()
    let active = true
    setState((previous) => ({ data: previous.data, loading: true, error: null }))

    taskRef
      .current(controller.signal)
      .then((data) => {
        if (!active) return
        setState({ data, loading: false, error: null })
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted) return
        const normalized =
          error instanceof Error ? error : new Error(typeof error === 'string' ? error : 'Unknown error')
        setState({ data: null, loading: false, error: normalized })
      })

    return () => {
      active = false
      controller.abort()
    }
  }, [key, attempt])

  const retry = useCallback(() => setAttempt((value) => value + 1), [])

  return { ...state, retry }
}
