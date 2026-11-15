import { createContext, useContext, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export const AnalysisControlsTarget = createContext<HTMLDivElement | null>(null)

export function AnalysisControlsPortal({ children }: { children: ReactNode }) {
  const target = useContext(AnalysisControlsTarget)
  return target ? createPortal(children, target) : null
}
